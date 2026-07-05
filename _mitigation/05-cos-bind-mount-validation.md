# 05 — cos bind-mount source validation

**Mitigates:** `02-security-audit.md` C1 (unvalidated bind-mount source → host
root). This was the single highest-severity finding.

## The problem
`Mount.validate()` checked only `type ∈ {bind,volume}` and non-empty
source/target. Nothing stopped
`mounts=[{source:"/var/run/docker.sock",target:...}]` (→ the "sandboxed"
container talks to the daemon and launches a `--privileged -v /:/host` container
= instant host root) or `source:"/"` (→ direct host-filesystem tamper). This
defeated every other cos control.

## The fix
`cos/src/cos/core/spec.py` — bind mounts whose source resolves into a
host-sensitive path are now rejected at spec-validation time (so both the MCP
tools and the CLI enforce it):

```python
_FORBIDDEN_BIND_PREFIXES = ("/proc", "/sys", "/dev", "/boot", "/etc", "/var/run", "/run")
# checked against BOTH normpath(source) and realpath(source), and the prefix set
# includes each path's realpath — so macOS symlinks (/etc → /private/etc) and
# `..`/symlink tricks can't slip past. Any */docker.sock and "/" are also denied.
```

`Mount.validate()` raises `SpecError` for a forbidden bind; `volume`-type mounts
(named volumes) are unaffected.

## Verification
- `test_forbidden_bind_mounts_rejected` — `/var/run/docker.sock`, `/`, `/proc`,
  `/sys/kernel`, `/etc/passwd`, and a `../` traversal all raise `SpecError`.
- `test_ordinary_bind_and_volume_mounts_allowed` — a project path and a named
  volume still pass.

## Residual (still open — bigger change, own pass)
This is a **deny-list**, not a workspace **allow-list**. It closes the known
host-escape vectors (docker.sock, `/`, kernel/device/credential dirs) but a
determined mount of some other sensitive host path is still possible. The audit's
stronger recommendation — confine bind sources to a configured workspace root —
is deferred with the `hardened` profile because it needs a cos config layer and
would change legitimate bind-mount ergonomics. Combined with 04's
`no-new-privileges` and the existing `network=none` default, the practical host-
root path (mount the docker socket) is now closed.
