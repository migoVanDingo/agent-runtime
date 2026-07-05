# 02 — `_find` scoped to managed containers

**Mitigates:** `02-security-audit.md` M5.

## The problem
`DockerBackend._find(name)` — the resolver behind `exec`, `logs`, `stop`, and
`rm` — filtered on `cos.name` **only**, dropping the `cos.managed=true` scope
that `list`/`reap`/`prune` correctly apply. Any container carrying a matching
`cos.name` label (even one cos never created) became exec/stop/rm-able through
cos.

## The fix
`cos/src/cos/core/backend.py` — `_find` now requires both labels (docker-py
list-form `filters` = AND):

```python
conts = self.client.containers.list(
    all=True, filters={"label": [f"{L.MANAGED}=true", f"{L.NAME}={name}"]})
```

## Verification
New live test `test_find_ignores_unmanaged_lookalike` — starts a raw container
carrying only `cos.name` (no `cos.managed`) and asserts `_find` returns `None`.

## Residual
Labels are still client-settable, so a caller could forge `cos.managed=true` +
`cos.name` to make cos adopt a look-alike. That is the broader "labels are
trusted" property of the label-as-state model (documented in the audit's Low
findings and the cos CLAUDE security note); server-side ownership is a larger
change deferred to any future multi-tenant story.
