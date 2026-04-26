# 0047 — Git Toolset

## Scope

A `git` toolset for read-only source control introspection. All tools shell out
to the system `git` binary and work against any repository path (defaults to
current working directory). Write operations (commit, push, reset, checkout)
are explicitly out of scope — `bash_exec` handles those with appropriate
ESCALATE prompting.

---

## Tools

### `git_status`

Working tree and staging area status.

**Inputs:**
- `repo_path` — path to repo (default: current working directory)

**Output:** Git status output formatted as text — branch name, tracked/untracked
files, staged/unstaged changes.

---

### `git_log`

Commit history.

**Inputs:**
- `repo_path` — default: cwd
- `limit` — number of commits (default 20, max 200)
- `branch` — branch or ref to log (default: current branch)
- `file` — optional path; restricts log to commits touching this file
- `oneline` — boolean, default false; compact one-line format

**Output:** Formatted commit list — hash, author, date, subject. Truncated at
`limit`.

---

### `git_diff`

Show changes between working tree, index, and commits.

**Inputs:**
- `repo_path` — default: cwd
- `ref` — commit reference, branch, or range (e.g. `"HEAD~3..HEAD"`, `"main"`)
- `staged` — boolean, default false; diff staged changes
- `file` — optional; restrict to specific file
- `stat` — boolean, default false; show only summary stats (--stat)

**Output:** Unified diff text, or stat summary if `stat=true`. Capped at 50 kB.

---

### `git_show`

Show the contents of a specific commit.

**Inputs:**
- `repo_path` — default: cwd
- `ref` — commit hash, tag, or branch (default: HEAD)
- `stat` — boolean; show stat only
- `file` — optional; restrict to one file in the commit

**Output:** Commit metadata + diff. Capped at 50 kB.

---

### `git_blame`

Annotate file lines with commit info.

**Inputs:**
- `repo_path` — default: cwd
- `file` (required) — path relative to repo root
- `start_line` — optional first line
- `end_line` — optional last line

**Output:** Annotated file with commit hash, author, date per line.

---

### `git_branch`

List branches.

**Inputs:**
- `repo_path` — default: cwd
- `all` — boolean, default false; include remote branches
- `verbose` — boolean, default false; show last commit per branch

**Output:** Branch list with current branch marked.

---

### `git_stash`

Inspect stash entries.

**Inputs:**
- `repo_path` — default: cwd
- `action` — `list` (default) or `show`
- `index` — stash index for `show` (default 0)

**Output:** Stash list or stash diff.

---

## Guard

All git tools are read-only. No ESCALATE or BLOCK required. They run git
commands that cannot modify state.

---

## Routing Rules

```python
GIT = Toolset(
    name="git",
    planning_note=(
        "Use git_status to check the working tree state. "
        "Use git_log to browse commit history. "
        "Use git_diff to see changes. "
        "Use git_show for a specific commit. "
        "Use git_blame to see who changed what. "
        "For write operations (commit, push, checkout), use bash_exec."
    ),
    rules=[
        any_keyword(
            "git", "commit", "branch", "diff", "log", "blame", "status",
            "stash", "show commit", "git history", "who changed",
            "what changed", "merge", "rebase", "working tree",
        ),
        lambda msg, _: bool(re.search(
            r"\bgit\s+\w+\b|\bcommit\s+history\b|\bworking\s+tree\b",
            msg, re.IGNORECASE,
        )),
    ],
)
```

---

## ActionType

Adds `GIT = "git"` to `ActionType` enum and `PLAN_JSON_SCHEMA`.

---

## Error Handling

All tools return `Error: git is not installed` if the binary is not found.
All tools return `Error: not a git repository` if the path is not inside a
git repo (detected via `git rev-parse --git-dir` exit code).

---

## Dependencies

| Dependency | Already present? |
|-----------|----------------|
| `subprocess` | Yes (stdlib) |

No new pip dependencies.

---

## Files

| File | Change |
|------|--------|
| `src/tools/implementations/git/__init__.py` | New |
| `src/tools/implementations/git/git_status.py` | New |
| `src/tools/implementations/git/git_log.py` | New |
| `src/tools/implementations/git/git_diff.py` | New |
| `src/tools/implementations/git/git_show.py` | New |
| `src/tools/implementations/git/git_blame.py` | New |
| `src/tools/implementations/git/git_branch.py` | New |
| `src/tools/implementations/git/git_stash.py` | New |
| `src/tools/toolsets.py` | Add GIT toolset + imports |
| `src/planning/schema.py` | Add `ActionType.GIT` |
| `config.yml` | Add `git` to `toolset_descriptions` |
