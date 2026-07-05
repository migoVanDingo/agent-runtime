# 04 — cos default resource/privilege hardening

**Mitigates:** `02-security-audit.md` H4 (no cap-drop / pids / default limits).
Partial mitigation — the non-breaking subset.

## The problem
`DockerBackend._create_kwargs` never set `pids_limit`, `no-new-privileges`, or a
default memory/cpu cap. `spec.limits` defaulted to all-`None`, so a container ran
with **unlimited pids/mem/cpu and default Linux capabilities** — a fork bomb
(`:(){ :|:& };:`) or a runaway allocation could take down the host.

## The fix
`cos/src/cos/core/backend.py` — every container now gets always-on, non-breaking
hardening; a spec's own limits still override the resource caps:

```python
_DEFAULT_PIDS_LIMIT = 512
_DEFAULT_MEM_LIMIT = "2g"
_DEFAULT_CPUS = 2.0
...
"pids_limit": _DEFAULT_PIDS_LIMIT,          # kills fork bombs
"security_opt": ["no-new-privileges"],      # kills setuid escalation
...
kwargs["mem_limit"] = spec.limits.memory or _DEFAULT_MEM_LIMIT
kwargs["nano_cpus"] = int((spec.limits.cpus or _DEFAULT_CPUS) * 1e9)
```

## Why only this subset
`cap_drop=ALL` and read-only rootfs were **deliberately not** made default —
they break many stock images (the reason they were deferred originally). They
remain future work as an opt-in `hardened` profile. The three defaults shipped
here (`pids_limit`, `no-new-privileges`, resource caps) close the fork-bomb / OOM
/ setuid-escalation vectors without breaking normal images.

## Verification
New live test `test_default_hardening_applied` — inspects a container's
`HostConfig` and asserts `PidsLimit == 512`, `Memory > 0`, and
`no-new-privileges` in `SecurityOpt`.

## Residual (still open — bigger change)
- **cap-drop / read-only rootfs** — the `hardened` opt-in profile (audit H4 tail).
- The `2g` / `2.0`-cpu defaults are module constants (cos has no config file);
  raise per-workload via `spec.limits`. A cos config layer would let the operator
  set global defaults.
