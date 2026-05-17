# 0058 - Runtime Refactor Phase 3: Path Policy For File Tools

## Goal

Add a centralized path policy so sandboxing shell commands is not the only filesystem boundary.

## Implemented

- Added `runtime.policy` package.
- Added `PathPolicy` and `PathPolicyDecision`.
- Added `check_path_allowed(path, operation)` helper.
- Added workspace and allowed root handling for:
  - `read`
  - `write`
  - `delete`
- Applied path policy to core file tools:
  - `read_file`
  - `read_file_lines`
  - `write_file`
  - `delete_file`
  - `delete_directory`
  - `copy_file`
  - `move_file`
  - `list_files`
  - `walk_directory`
  - `make_directory`
- Added default allowed temp/store roots in `config.yml`.
- Added unit coverage for allow/deny path decisions.

## Behavior Notes

File tools now deny paths outside the configured workspace and allowed roots. This is intentionally stricter than the old behavior.

Default allowed roots include:

- project workspace (`.`)
- `/tmp`
- `/private/tmp`
- `_store`

Those defaults preserve current workflow patterns that use temp files and artifact storage while still blocking obvious sensitive paths such as `/etc/passwd`.

## Remaining Work

- Route path policy decisions through `UserGate` for approval-capable operations.
- Emit structured `policy.decision` events.
- Apply path policy to document/data/git tools where they touch filesystem paths.
- Add symlink-focused tests.

## Verification

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
python3 -m compileall -q src
```
