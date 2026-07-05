# 09 — gcs_download host-write confinement

**Mitigates:** `02-security-audit.md` C6 (arbitrary host-file write) and H10
(new-file download bypasses the gate).

## The problem
`GCSDownload.execute` wrote to `Path(local_path).expanduser()` — no root
confinement, `..`/absolute accepted — so an allowed-bucket object (or one the
agent uploaded) could be written to `~/.ssh/authorized_keys`, a LaunchAgent
plist, etc. (C6). Worse, a **new-file** download was classified as operation
`"stat"` (a read op), so it never hit the UserGate — only overwrites were gated
(H10).

## The fix (`arc-plugin-gcs`)
- **Confinement** (`tools/file_ops.py::_confine_download`): downloads write ONLY
  under `ToolContext.download_dir`. `local_path` is resolved relative to it;
  absolute paths and any `..`/symlink result that escapes the dir raise
  `ToolError`. `~` is NOT expanded (a literal `~` becomes a harmless subdir, not
  the real home). The parent dir is `mkdir`'d under the root.
- **Config** (`plugin.py`): `download_dir` config key, default
  `<arc_home>/downloads` (derived from `build_ctx.sessions_dir.parent`).
  Threaded through `GCSPlugin` → `ToolContext`.
- **Op reclassification** (H10): a new-file download is now `download_new` (added
  to `_MUTATION_OPS` in `escalation.py`), so it goes through `gate_and_reserve`
  at the `mutations` level instead of being a silent read. The redundant
  `operation if local.exists() else "stat"` double-bug is gone.

## Verification
- `test_download_rejects_path_escape` — `/etc/passwd`, `../escape`, `a/../../escape`
  all raise.
- `test_download_new_is_a_gated_write_op` — `download_new ∈ _MUTATION_OPS`; a deny
  gate at the `mutations` level blocks a new download and no file is written.
- Existing download tests updated to the relative-path model. **98 gcs tests pass.**

## Residual
- Default escalation level is `destructive`, so `download_new` (a mutation) is
  gated only when the operator sets `escalation_level: mutations`. The
  confinement is the primary protection at all levels; the gate is defense in
  depth.
- **M11** (inert default budget) is unrelated and still open.
