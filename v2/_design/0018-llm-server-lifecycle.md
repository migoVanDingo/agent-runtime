# 0018 — `arc llm` lifecycle (native llama-server management)

## Motivation

After 0015, arc can talk to a running `llama-server` over HTTP.  But
`llama-server` is a single-model process: switching from Llama 3.1 8B to
Qwen 2.5 Coder 32B means stopping the server, restarting it with a
different `-m` flag, and waiting for the new model to load.  Doing that
by hand every time you want to try a different model is enough friction
that you stop trying.

The user setup constraints:
- No `sudo` on the inference host — system-wide installs (`systemd`,
  `/usr/local/bin`) are off the table.
- Server lives in a Python virtualenv alongside arc, or in a user-owned
  build directory like `~/llama.cpp/build/bin/llama-server`.
- One GPU, one model loaded at a time (VRAM constraint).  No need to
  juggle multiple servers concurrently.

This phase ships an `arc llm` subcommand family that owns the
`llama-server` process lifecycle for the local user, plus a
`~/.arc/llm_servers.yml` registry of available models.  The 0017 picker
calls into this when the user picks a `llama_cpp` model that isn't
currently loaded.

---

## Scope

In:
- New CLI subcommand family:
  - `arc llm list`               — list models from the registry; mark which (if any) is running
  - `arc llm status`             — show details about the running server (model, pid, uptime, /health)
  - `arc llm start <model-id>`   — spawn `llama-server` with the registry's config for that id; wait for /health=ok
  - `arc llm stop`               — graceful SIGTERM → SIGKILL fallback after timeout
  - `arc llm restart <model-id>` — `stop` then `start`
  - `arc llm logs [--tail N]`    — print recent stderr/stdout from the running server
- `~/.arc/llm_servers.yml` registry — paths to `llama-server` binary(s),
  per-model `-m` + extra args
- Process tracking via PID file at `$ARC_HOME/llm/<server-id>.pid`,
  log capture at `$ARC_HOME/llm/<server-id>.log`
- Integration with the 0017 picker: when picking a `llama_cpp` model
  that doesn't match what's running, offer to swap
- Health-check polling on startup with a configurable timeout
- venv-friendly: works with either the user-compiled `llama-server` C++
  binary OR `python -m llama_cpp.server` (the `llama-cpp-python[server]`
  package, which pip-installs without sudo)

Out (deferred):
- Multiple concurrent servers on different ports.  Single GPU, one model
  at a time is the common case.  If a user really wants two, they can
  shell out to a second `llama-server` directly and arc's picker just
  points at the port.
- Auto-restart on crash.  Keep it simple; if the server dies, surface
  the error and let the user run `arc llm start` again.
- Auto-download of .gguf files.  User curates models in
  `llm_servers.yml`; arc doesn't fetch them.
- Speculative decoding / draft models as a first-class registry feature.
  Pass-through via `extra_args` works fine today.
- A daemon mode that auto-loads the right model when arc starts a
  session.  Tempting magic but it conflates session lifecycle with
  server lifecycle.  Keep explicit start/stop.

---

## Architecture

```
src/arc/
  llm/
    __init__.py             ← re-exports run_llm_command()
    registry.py             ← llm_servers.yml loader, ServerSpec dataclass
    process.py              ← Popen wrapper, PID file, SIGTERM/KILL, log capture
    health.py               ← /health polling with timeout
    commands.py             ← `list`, `status`, `start`, `stop`, `restart`, `logs` dispatchers
  cli.py                    ← +`llm` subcommand wiring
  defaults.py               ← +DEFAULT_LLM_SERVERS_YAML
  setup/picker.py           ← (0017 file) calls into llm.commands when a llama_cpp model is picked
tests/unit/test_llm_registry.py
tests/unit/test_llm_process.py
tests/integration/test_llm_lifecycle.py
```

No new top-level deps.  Process management uses `subprocess.Popen` +
`os.kill`; health probing reuses `httpx` (already pulled in by 0014).

### Registry file (`~/.arc/llm_servers.yml`)

```yaml
# arc llm-server registry — drives `arc llm` commands and the 0017 picker.
# Pick the binary you have available; both work.

binary:
  # Option A: user-compiled llama.cpp (faster, more knobs)
  #   path: ~/llama.cpp/build/bin/llama-server
  #   kind: llama_cpp

  # Option B: llama-cpp-python's bundled server (pip-installable, no sudo)
  path: python
  kind: llama_cpp_python                # uses `python -m llama_cpp.server`

# Default args appended to every invocation.  Override per-model below.
default_args:
  - "--host"
  - "127.0.0.1"
  - "--port"
  - "8080"

# How long to wait for /health to flip to "ok" after starting.
# Big models on cold cache can take 60-120s.  Bump if needed.
startup_timeout_seconds: 180

# Models you have downloaded.  Each entry becomes a pickable choice
# in both `arc llm` and the 0017 picker.
models:
  - id: llama-3.1-8b
    label: "Llama 3.1 8B Instruct (Q4)"
    gguf: ~/models/llama-3.1-8b-instruct.Q4_K_M.gguf
    extra_args:
      - "-c"
      - "8192"
      - "-ngl"
      - "99"

  - id: qwen-2.5-coder-32b
    label: "Qwen 2.5 Coder 32B (Q4)"
    gguf: ~/models/qwen2.5-coder-32b-instruct.Q4_K_M.gguf
    extra_args:
      - "-c"
      - "16384"
      - "-ngl"
      - "99"
```

Loader (`registry.py`):

```python
@dataclass(frozen=True)
class ServerBinary:
    path: str                   # "python" or "/home/me/llama.cpp/build/bin/llama-server"
    kind: str                   # "llama_cpp" | "llama_cpp_python"

@dataclass(frozen=True)
class ServerModel:
    id: str
    label: str
    gguf: Path                  # expanded ~ and resolved
    extra_args: list[str]

@dataclass(frozen=True)
class Registry:
    binary: ServerBinary
    default_args: list[str]
    startup_timeout_seconds: int
    models: list[ServerModel]
    source_path: Path

    def find(self, model_id: str) -> ServerModel:
        """Lookup; raises RegistryError with a clear list of known ids."""
```

The two `binary.kind` values produce different argv:

| kind | argv |
|---|---|
| `llama_cpp` | `[<binary.path>, "-m", <gguf>, *default_args, *extra_args]` |
| `llama_cpp_python` | `[<binary.path>, "-m", "llama_cpp.server", "--model", <gguf>, *translated_default_args, *translated_extra_args]` |

llama-cpp-python's CLI uses long-form options (`--host`, `--port`,
`--n-ctx`, `--n-gpu-layers`) instead of llama.cpp's short flags
(`-c`, `-ngl`).  The translator handles the common ones (port, host,
context length, GPU layers); unknown flags pass through untouched, on
the assumption that future llama-cpp-python releases stay aligned with
llama.cpp arg names.

### Process management (`process.py`)

```
$ARC_HOME/llm/
  current.pid                  ← PID + model-id + started-at, one line
  current.log                  ← combined stdout+stderr (rotated lazily at 50 MB)
```

Single-server model: there's at most one running server per ARC_HOME.
The PID file's presence is the source of truth.

Start:
1. Read registry, find requested model, build argv.
2. If `current.pid` exists and points to a live process → either:
   - Same model → no-op, report "already running."
   - Different model → return error "model X is running; run `arc llm stop` first or use `arc llm restart`."
3. Open `current.log` for append; `subprocess.Popen(argv, stdout=log, stderr=log, start_new_session=True)`.
4. Write `current.pid` (pid + model-id + ISO timestamp).
5. Poll `/health` every 1s until either `"status": "ok"` or
   `startup_timeout_seconds` elapses.  Report progress lines so user
   sees the loading bar move.
6. On timeout: don't kill — the server may still come up, just exit with
   a "took longer than expected; check `arc llm logs`."

Stop:
1. Read `current.pid`.  Missing → "no server running."
2. `os.kill(pid, SIGTERM)`.
3. Poll every 0.5s for up to 10s; if process exits, remove PID file.
4. If still running after 10s → `os.kill(pid, SIGKILL)`, remove PID file.
5. If pid doesn't exist (stale file) → remove PID file silently.

`start_new_session=True` (POSIX `setsid()`) detaches the child so
killing arc itself doesn't take down the server.  The server outlives
the CLI invocation, which is exactly what we want.

### Health checking (`health.py`)

llama.cpp and llama-cpp-python both expose `GET /health`:
- llama.cpp returns `{"status": "ok"}` or `{"status": "loading model"}`.
- llama-cpp-python is less consistent across versions — accept anything
  with HTTP 200 as healthy after a 2s warmup.

```python
def wait_for_healthy(*, base_url: str, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/health", timeout=2)
            if resp.status_code == 200:
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                if body.get("status") in (None, "ok"):
                    return True
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(1)
    return False
```

---

## CLI surface

```
arc llm list
  ID                        LABEL                       SIZE       STATUS
  llama-3.1-8b              Llama 3.1 8B Instruct (Q4)  4.7 GB     running (pid 12453, 14m uptime)
  qwen-2.5-coder-32b        Qwen 2.5 Coder 32B (Q4)     19.8 GB    available

arc llm status
  Running: qwen-2.5-coder-32b
  Binary:  /home/me/llama.cpp/build/bin/llama-server
  PID:     12453
  Started: 2026-05-23 18:42:11 (14m ago)
  /health: ok
  Listening: http://127.0.0.1:8080

arc llm start qwen-2.5-coder-32b
  Stopping current (llama-3.1-8b)…  done.
  Starting qwen-2.5-coder-32b…
    cmd: /home/me/llama.cpp/build/bin/llama-server -m /home/me/models/qwen2.5-coder-32b.Q4_K_M.gguf -c 16384 -ngl 99 --host 127.0.0.1 --port 8080
    waiting for /health: ok  ████████░░░░  47s elapsed
    ready (took 51s).
  Logs: $ARC_HOME/llm/current.log

arc llm stop
  Sending SIGTERM to pid 12453…
  Stopped.

arc llm logs --tail 50
  <last 50 lines of current.log>
```

### Picker integration (0017)

When the user runs `arc setup` and picks a `llama_cpp` model:

1. `arc setup` reads `llm_servers.yml` and shows the model list there
   instead of (or in addition to) the live `/v1/models` discovery.
   Live discovery still happens to confirm what's actually loaded — but
   the picker source-of-truth is the registry.

2. After model selection, `arc setup` checks `arc llm status`:
   - **Right model already running** → write config, done.
   - **No server running** → "Start llama-server with this model now? [Y/n]" → on yes, calls into `commands.start(model_id)`.
   - **Different model running** → "Stop current (X) and start (Y)? [Y/n]" → calls `commands.restart(model_id)`.

3. All three branches end with a written `config.yml` and either a
   running server or a clear next step for the user.

This is the headline UX — the user goes from "I want to try Qwen
Coder" to "session running against Qwen Coder" in one menu and ~60s of
model load, without ever typing `llama-server -m ...`.

---

## Failure modes

| Failure | Behavior |
|---|---|
| Binary path doesn't exist | Clear error at start: "binary path 'X' not found; edit ~/.arc/llm_servers.yml." |
| GGUF file doesn't exist | Same — fail at start, name the file. |
| Port already in use | Server exits immediately; log will show the bind error; CLI reads recent log on failure and prints the tail. |
| Server starts but never returns /health=ok within timeout | Don't kill it (model may still be loading); print "took longer than expected; check `arc llm logs`" and exit non-zero. |
| Stale PID file (process died but file wasn't removed) | On any command, validate the pid exists with `os.kill(pid, 0)`; if not, clean up silently and proceed. |
| User pressed Ctrl+C during `arc llm start` | `start_new_session=True` means the child survives; arc exits cleanly; user can re-run `arc llm status` to check. |
| Two concurrent `arc llm start` invocations | Race on PID file creation.  Use atomic `O_CREAT | O_EXCL` open; second invocation sees the file and reports "another start in progress." |
| llama-cpp-python and llama.cpp arg mismatch | The translator covers port/host/n_ctx/n_gpu_layers; unknown args pass through.  If passthrough fails on a kind mismatch, server logs it; tail-on-failure surfaces it. |

---

## Observability

`arc llm` is a CLI tool, not a session.  It doesn't write to
`events.jsonl` — instead, its log goes to `$ARC_HOME/llm/current.log`.

But once a session is running against the server, every LLM call still
emits `llm.call.*` events into `events.jsonl` as today.  The server's
PID and model are *not* recorded in the session events — that
information lives in the server-side log, not the agent.  This is
intentional: the session is provider-agnostic and shouldn't know
whether the model is local or cloud.

The optional `provider_load_ms` metadata on `llm.call.completed`
(promised in 0014) handles the "did this turn pay a cold-load cost"
visibility for the session log itself.

---

## File layout

```
src/arc/llm/__init__.py
src/arc/llm/registry.py
src/arc/llm/process.py
src/arc/llm/health.py
src/arc/llm/commands.py
src/arc/cli.py                       ← +`llm` subcommand
src/arc/defaults.py                  ← +DEFAULT_LLM_SERVERS_YAML
src/arc/setup/picker.py              ← (0017) +integration with arc.llm.commands
tests/unit/test_llm_registry.py
tests/unit/test_llm_process.py
tests/integration/test_llm_lifecycle.py
```

---

## Optional dep: llama-cpp-python

For users who can't or don't want to compile llama.cpp themselves:

```
pip install 'llama-cpp-python[server]'
```

Add to `pyproject.toml` as an optional extra:

```toml
[project.optional-dependencies]
llama_cpp = ["llama-cpp-python[server]>=0.3"]
```

So `pip install -e .[llama_cpp]` installs it; the default install
doesn't pay the cost.  The registry's `binary.kind: llama_cpp_python`
just shells out to `python -m llama_cpp.server` from whatever venv is
active.

---

## Test plan

> Unit tests run anywhere (mocked subprocess, mocked httpx).  Lifecycle
> integration test gated on `LLAMA_CPP_BINARY` + `LLAMA_CPP_TEST_MODEL`
> env vars; CI skips by default.

Unit (`test_llm_registry.py`):
1. Parse a valid `llm_servers.yml` with both binary kinds
2. Lookup by model id; unknown id raises `RegistryError` with known-ids list
3. `~` in gguf paths is expanded to the user home dir
4. Missing `binary.path` → clear error
5. Missing `models` (empty list) is allowed; raises only when a lookup happens
6. Argv builder produces correct shape for `llama_cpp` kind
7. Argv builder translates port/host/n_ctx/n_gpu_layers for `llama_cpp_python` kind

Unit (`test_llm_process.py`):
1. `start` with no running server → spawns, writes PID file, returns
   after /health=ok (mocked Popen + mocked health-poller)
2. `start` with same model already running → no-op, returns existing pid
3. `start` with different model already running → returns error
4. `stop` sends SIGTERM, polls, removes PID file
5. `stop` with no PID file → no-op, exits 0 with "nothing to stop"
6. Stale PID file (process doesn't exist) → cleaned up silently
7. Race: two simultaneous starts → one wins via O_EXCL, other errors clearly
8. Log file is opened in append mode (existing content survives restart)

Integration (`test_llm_lifecycle.py`):
1. Skip unless `LLAMA_CPP_BINARY` set
2. `arc llm start <test-model>` → server actually comes up, /health
   returns ok within timeout
3. `arc llm status` shows the running model
4. `arc llm stop` shuts it down cleanly within 10s
5. PID file removed after stop
6. `arc llm logs --tail 5` returns something

Smoke:
- On Ubuntu: edit `llm_servers.yml`, add a real model, `arc llm start`,
  `arc run "hi"` against it, `arc llm stop`.

---

## Open questions

1. **`arc llm start` block until ready, or background-and-return?**
   Resolution: block until /health=ok.  Showing a progress line is nicer
   than the user wondering whether anything is happening.  The
   underlying process is already detached (setsid), so the *server*
   keeps running after the CLI exits; we just don't return until
   the server is responsive.

2. **What if the user runs llama-server outside arc and arc doesn't
   know?**  The PID file won't exist, so `arc llm status` reports "no
   server running" even though there *is* one on port 8080.  That's
   fine — arc isn't trying to be the only path; the session just talks
   HTTP to whatever's there.  The picker's live `/v1/models` discovery
   still works against externally-managed servers.  Document this in
   the file's preamble comment.

3. **Should `arc llm` work without ARC_HOME being initialized?**
   Resolution: no.  `arc llm` reads `llm_servers.yml` from ARC_HOME, so
   it implicitly depends on bootstrap having run.  If
   `llm_servers.yml` doesn't exist, write the shipped default and
   continue (mirroring how `arc setup` handles a missing config).

4. **Process restart semantics if arc itself is restarted while a
   server is running.**  No special handling — the PID file persists,
   `arc llm status` re-discovers the running server next time arc
   starts.  setsid means the server survives terminal disconnection,
   ssh logout, etc.

---

## State

Landed.

---

## Implementation notes

1. **`subprocess.Popen(start_new_session=True)` was the critical
   detach.**  Without it, killing the `arc llm start` invocation
   (Ctrl-C, terminal close) takes the server down with it.  With it,
   the server runs in its own session and survives.  No daemon
   needed; no double-fork dance.

2. **PID-file races resolved via `O_CREAT | O_EXCL`.**  Two concurrent
   `arc llm start` invocations now produce a clean "another start in
   progress" error rather than corrupting the pid file or leaving
   zombies.  See `_write_pid_file` in `process.py`.

3. **`_pid_alive` does double duty.**  Both `read_pid_file` (to drop
   stale pid files) and the post-SIGTERM polling loop in `stop()` call
   it.  In tests that mock it, this means a single mocked return value
   isn't enough — `side_effect=[True, False]` is the right shape
   ("alive when reading file, dead after signal").  Pattern noted in
   `test_llm_process.py`.

4. **`wait_for_healthy` import path matters for patching.**  Tests
   patch `arc.llm.health.wait_for_healthy` (the source), not
   `arc.llm.process.wait_for_healthy` (where it's locally imported).
   Same lesson as 0017's prompt_toolkit dialog patches.

5. **`llama-cpp-python` arg translation kept conservative.**  Only
   five short flags are mapped to their long-form equivalents (`-m`,
   `-c`, `-ngl`, `-t`, `-b`).  Everything else passes through.  This
   handles the 90% case and won't silently break when llama-cpp-python
   adds new options; users who hit a translation gap get a clear log
   from the server itself, tailed by `arc llm logs`.

6. **Picker integration is *fire-and-forget* on the picker side.**
   `_maybe_swap_llm_server` returns a status string ("started",
   "swapped", "kept", "declined", "skipped", None) that `SetupResult`
   exposes for the CLI to render.  The picker doesn't try to recover
   from a failed start — if start_server returns non-zero, the picker
   labels the action "declined" and the user has the config written
   already.  They can re-run `arc llm start` later from the registry
   they edited.

7. **Default `binary.kind` in `llm_servers.yml` is `llama_cpp_python`.**
   Reasoning: it's the only option a no-sudo user can install via pip
   without compiling anything.  Compiled `llama-server` users will
   set their own path + `kind: llama_cpp` and never see the default.
