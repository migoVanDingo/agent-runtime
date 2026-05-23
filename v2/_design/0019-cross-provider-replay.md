# 0019 — Cross-provider replay + comparison

## Motivation

Replay (0004) lets you take a recorded session and re-run it, either
deterministically from the recorded `.raw` payloads (mode 2) or with a
fresh LLM call (mode 3).  Today, mode 3 calls the *same* provider the
original used.

The interesting question — and the one that justifies running a
$2,000-of-electricity inference host — is:

> *"Claude Sonnet 4.6 just solved this hard task.  Can Qwen 2.5 Coder
> 32B (or Llama 3.1 70B, or Gemini Flash, or anything else I have)
> solve it too?  How does it compare on tool-use quality, token count,
> wall time, cost?"*

This phase ships that capability.  The replay engine grows three new
features:

1. **Provider/model override** in mode 3 — re-run with any provider
   from the picker, not just the original.
2. **Batch mode** — fork into N parallel replays against N different
   models, all from the same starting session.
3. **A TUI-driven launcher and side-by-side comparison view** —
   selecting "what to replay against" through a menu, not a 60-char
   flag combo.

The CLI flags still exist (CI, scripting), but the **TUI menu is the
headline UX**.

---

## Scope

In:
- `arc replay` invoked with no session id → drops into the TUI replay
  menu (session picker → start-turn → mode → provider → model → batch
  picks → max-cost → confirm → launch)
- `/replay` slash command inside the running TUI → same menu
- `--override-provider <name>` `--override-model <id>` flags on
  `arc replay <id> --live-llm`
- `--against provider:model,...` flag for batch replays (forks N
  parallel children, each writes its own session dir)
- `--max-cost-usd <N>` guardrail — implemented as a new `max_cost`
  plugin enabled only for replay-with-override sessions; aborts the
  session cleanly with a clear `events.jsonl` entry when the running
  total exceeds the cap
- `arc compare <id1> <id2> [<id3> …]` subcommand — renders a Rich
  side-by-side comparison of two or more sessions (turn-by-turn tool
  calls, response text, summary metrics)
- Tools re-execute live (option 1 from the 4.1 design discussion).
  Tool-result determinism is not a goal; reasoning-quality comparison is.
- Cost rendering during the run via the pricing table (per-replay
  running total in the progress view)

Out (deferred):
- Per-tool replay/live policy (option 3 from 4.1).  If a session
  contains a non-deterministic tool the user wants to pin, they can
  swap it out by editing `config.yml` for the replay run.
- Async / multi-process scheduling for cloud providers.  Batch fan-out
  uses `subprocess.Popen` and waits; "true parallelism" with rate-limit
  coordination is a follow-up if anyone needs to compare against 10+
  models at once.
- A web UI for comparison.  Rich tables in the terminal cover the
  80% case; if someone wants Plotly graphs that's a separate phase.
- Saved benchmark suites ("run my 20 hard tasks against the new
  model").  Each replay is one session-id; batching a list of sessions
  is a thin loop on top.

---

## Architecture

```
src/arc/
  replay/
    loader.py                 ← unchanged
    provider.py               ← unchanged (ReplayProvider serves recorded responses)
    tools.py                  ← unchanged
    diff.py                   ← unchanged for the deterministic-replay path
    override.py               ← NEW — builds an override ProviderConfig from CLI/menu input
    batch.py                  ← NEW — forks N child `arc replay` invocations, collects sessions
    compare.py                ← NEW — renders N-way comparison view
  plugins/
    max_cost/                 ← NEW — after_llm_call tallier; raises ReplayCostExceeded when over cap
  tui/
    replay_menu.py            ← NEW — prompt_toolkit menus for the replay flow
    compare_render.py         ← NEW — Rich layout for the comparison view
  cli.py                      ← +`compare` subcommand, +menu trigger for `arc replay` with no args
tests/unit/test_replay_override.py
tests/unit/test_max_cost_plugin.py
tests/unit/test_replay_batch.py
tests/unit/test_compare_render.py
tests/integration/test_cross_provider_replay.py
```

### Override mechanics

`ReplayProvider` already exists for mode 2.  Mode 3 today calls into
`arc.providers.build()` with the original `ProviderConfig` from the
recorded `config.snapshot.yml`.

Override changes one thing: before `build()`, the loaded config's
provider section is mutated to the override values:

```python
def apply_override(
    cfg: Config,
    *,
    provider_name: str,
    model: str,
    api_key_env: str | None = None,
    base_url: str | None = None,
) -> Config:
    """Return a Config with the provider section swapped.

    Defaults for api_key_env and base_url come from the same registry
    the 0017 picker uses, so picking 'ollama' auto-fills the right
    api_key_env without the user typing it again.
    """
```

The replay engine then constructs a provider from the overridden
config, and the call proceeds exactly as a fresh session would — same
hooks, same plugins, same event emission.  The recorded user inputs
feed in via the existing mode-3 path; tools execute live (today's
behavior; no change).

The new session's `config.snapshot.yml` reflects the **override**, so
the replay session is self-contained — you can replay-the-replay later,
or diff its events against any other session, without needing to know
the original.

### Tool re-execution

Mode 3 already re-executes tools live.  Cross-provider replay does the
same.  Consequences:

- `bash_exec date` returns a different timestamp each run.  The model
  might react differently to "now" vs "an hour ago."  This is fine — we're
  not benchmarking timestamps, we're benchmarking reasoning.
- A model that asks for a different sequence of tools is exactly what
  we want to see — that's signal about how it approaches the problem.
- Tools that touch external state (writing files, hitting APIs) will
  do so for real.  Run replays from a clean workspace, or accept the
  side-effects, the same caveat as mode 3 today.

If a tool is non-deterministic in a way that makes the comparison
useless (e.g., a randomized network call), the user can disable that
tool in the replay session's config.  Out of scope for this design.

### `max_cost` plugin

```python
class MaxCostPlugin:
    """Track running USD cost across LLM calls; raise when the cap is
    exceeded.

    Enabled automatically when a replay specifies --max-cost-usd.  The
    plugin reads provider/model off the session context and pricing
    rates from the shared PricingTable.  Local providers always cost
    $0 (per 0014), so this is effectively a cloud-provider safety net.
    """

    def __init__(self, max_cost_usd: float, pricing_table: PricingTable) -> None: ...

    def after_llm_call(self, ctx, req, resp) -> None:
        rates = self._table.lookup_for(provider=ctx.session.provider_name,
                                        model=ctx.session.provider_model)
        if not rates:
            return  # unknown rate (most local providers) — no enforcement
        cost = (resp.input_tokens * rates["input_cost_per_token"]
                + resp.output_tokens * rates["output_cost_per_token"])
        self._running += cost
        if self._running > self._cap:
            raise ReplayCostExceeded(
                f"replay aborted: cost ${self._running:.4f} exceeds cap ${self._cap:.2f}"
            )
```

`ReplayCostExceeded` bubbles up the runtime and the session ends
cleanly with a `session.aborted` event recording the cap, the running
total, and the turn at which the cap was hit.

### Batch mode

```python
def run_batch(
    source_session_id: str,
    targets: list[tuple[str, str]],     # [(provider, model), ...]
    *,
    max_cost_usd: float | None,
    arc_home: Path,
) -> list[BatchResult]:
    """Fork N child `arc replay` invocations, one per target.

    Each child writes a new session dir.  Returns BatchResult per
    target (session_id, return_code, summary metrics) once all
    children exit.
    """
```

For **cloud targets** we fan out concurrently with `Popen`.  For
**llama.cpp targets** we serialize (one model loaded at a time) and
shell out to `arc llm start` (0018) between targets if the model
differs.  Ollama targets run concurrently against the same Ollama
server because Ollama auto-loads.

This sequencing logic is the messiest part of the design.  Pseudocode:

```python
def schedule(targets):
    cloud = [t for t in targets if t.provider in ("anthropic", "gemini", "openai")]
    ollama = [t for t in targets if t.provider == "ollama"]
    llama = [t for t in targets if t.provider == "llama_cpp"]

    # Cloud + ollama run concurrently
    parallel_handles = [_spawn(t) for t in cloud + ollama]

    # llama.cpp targets serialize (GPU = one model at a time)
    serial_results = []
    for t in llama:
        _ensure_llm_running(t.model)        # arc llm start ...
        serial_results.append(_run_and_wait(t))

    parallel_results = [_wait(h) for h in parallel_handles]
    return parallel_results + serial_results
```

### Comparison view (`arc compare`)

Three layouts; user picks per-invocation, defaults to "summary then
turn-by-turn" for two sessions and "summary only" for >2.

**Summary table** (always shown first):

```
arc compare 01JK2A... 01JK4F... 01JK4G...

Source: 01JK2A... — claude-sonnet-4-6 (original)
         "debug the segfault in main.c"

                              original              replay-1            replay-2
Provider/model                claude-sonnet-4-6     ollama/llama3.1:8b  ollama/qwen2.5-coder:32b
Turns                         8                     5  (-3)             7  (-1)
Tool calls                    19                    11 (-8)             16 (-3)
Input tokens                  34,521                51,233              28,901
Output tokens                 8,743                 6,201               9,118
Cost (USD)                    $0.18                 $0.00               $0.00
Wall time                     94 s                  47 s                118 s
Final stop reason             end_turn              end_turn            end_turn
Last response (truncated)     "Fix shipped: …"      "I think it's …"    "Bug was at …"
```

**Turn-by-turn diff** (two-session mode):

```
Turn 1 — user: "debug the segfault in main.c"
  original:  "I'll examine the source first."
             → ls(path=".") → 12 files
             → bash_exec(cat main.c) → 248 lines
  replay:    "Let me look at the code."
             → bash_exec(cat main.c) → 248 lines

Turn 2 — assistant (continued)
  original:  "The bug is on line 47 — uninitialized pointer."
  replay:    "Line 47 has a memory issue."

Turn 3 — ...
```

**Full event dump** (escape hatch, for debugging): two events.jsonl
files side-by-side, line-numbered.

Implementation: reuse `replay/diff.py`'s event normalization for the
turn-by-turn view; new `compare_render.py` does the Rich layout.

---

## TUI replay menu — the headline UX

`arc replay` with no session id, OR `/replay` inside the running TUI:

```
arc replay — pick a session and how to re-run it

Session (most recent first):
  ( ) 01JK1Q...  2026-05-22 14:22  gemini-2.5-pro          12 turns  42 tool calls
  (•) 01JK2A...  2026-05-23 09:14  claude-sonnet-4-6        8 turns  19 tool calls
  ( ) 01JK3B...  2026-05-23 18:42  ollama/llama3.1:8b       3 turns   5 tool calls
  ( ) older…

[enter] continue   [/] filter   [q] abort

→ Start from turn:
  (•) beginning (default)
  ( ) turn 3   (assistant: "looks like I need to read the file…")
  ( ) turn 5   (assistant: "found the bug in main.c")

→ Mode:
  ( ) Deterministic replay  (reuse recorded LLM responses — free, fast)
  (•) Live LLM              (call the model fresh; see how it does)

→ Provider:
  ( ) keep original (claude-sonnet-4-6)
  ( ) anthropic
  ( ) gemini
  (•) ollama
  ( ) llama_cpp

→ Model:   (querying http://localhost:11434/api/tags …)
  (•) llama3.1:8b
  ( ) qwen2.5-coder:32b
  ( ) hermes3:8b
  ( ) type a model id manually…

→ Add more models to run in parallel?  (batch mode — pick zero or more)
  [ ] anthropic / claude-haiku-4-5
  [ ] gemini / gemini-2.5-flash
  [ ] ollama / qwen2.5-coder:32b
  [ ] type a manual model id…

→ Max cost (USD):
  ( ) unlimited
  ( ) $1
  (•) $5
  ( ) $10
  ( ) custom…

→ Ready to launch?
  Source session:  01JK2A... — "debug the segfault in main.c"
  Start from turn: beginning
  Mode:            live LLM
  Targets (2):     ollama / llama3.1:8b
                   anthropic / claude-haiku-4-5
  Max cost:        $5.00
  Tools:           live re-execution
  Output:          $ARC_HOME/sessions/<new-id>/  (2 dirs, one per target)

[enter] launch   [b] back   [q] abort
```

While running, a Rich Live view shows turn-by-turn progress per target:

```
Replaying 01JK2A... against 2 targets…

  ollama/llama3.1:8b           [████████░░░░] turn 5/8     11 tool calls    $0.00
  anthropic/claude-haiku-4-5   [██████░░░░░░] turn 4/8      9 tool calls    $0.02 / $5.00

Press [Ctrl+C] to abort all.
```

When all targets finish, automatically drop into the comparison view.

### Implementation

`prompt_toolkit`'s `radiolist_dialog` + `checkboxlist_dialog` covers
every screen.  ~250 lines for the menu flow, ~150 for the
progress-live-view, ~200 for the comparison Rich layout.  Total
~600 lines for the new TUI surface.

The menu code lives in `tui/replay_menu.py`.  Both `arc replay` (no
arg) and the `/replay` slash command call into the same
`run_replay_menu(arc_home)` entry point.

---

## CLI surface (escape hatch)

```
# Mode 2 (deterministic) — unchanged from today
arc replay <session-id>

# Mode 3 same-provider — unchanged from today
arc replay <session-id> --live-llm

# NEW: mode 3 with override
arc replay <session-id> --live-llm \
    --override-provider ollama \
    --override-model qwen2.5-coder:32b \
    --max-cost-usd 5

# NEW: batch
arc replay <session-id> --against \
    ollama:llama3.1:8b,ollama:qwen2.5-coder:32b,anthropic:claude-haiku-4-5 \
    --max-cost-usd 10

# NEW: drop into the TUI menu (no session id)
arc replay
arc replay --menu       # equivalent

# NEW: compare existing sessions
arc compare <id1> <id2> [<id3> …]   # summary + turn-by-turn (2 sessions) or summary only (>2)
arc compare <id1> <id2> --full      # full event-by-event dump
```

`--against` parses `provider:model,provider:model` pairs; the colon is
the separator inside a pair and the comma between pairs.  Whitespace
allowed.  Use a manual model id by entering it verbatim (the comma is
the only forbidden character).

---

## Failure modes

| Failure | Behavior |
|---|---|
| Override provider unknown | Exit 2 with the same "known providers" list `build()` already emits. |
| Override model isn't in user's catalog.yml AND looks invalid (rejected by the provider on first call) | Replay session errors at turn 1 with the provider's error message; partial events are written; comparison view labels the run "errored at turn 1." |
| Max-cost cap hit mid-run | Session aborts cleanly; events.jsonl carries a `session.aborted` event with the cap and actual cost; comparison view shows "aborted (cost cap)" on that target. |
| Batch target's `llama_cpp` model isn't in `llm_servers.yml` (0018) | Exit 2 with the same "edit ~/.arc/llm_servers.yml" message `arc llm` shows. |
| User aborts (Ctrl+C) during batch | SIGTERM to all running children; each gets a `session.aborted`; the partial comparison view still renders for the completed targets. |
| Pricing table miss for an override target | Cost shows as "?" rather than "$0.00"; max-cost cap is not enforced for that target (with a warning).  Local providers are always $0 per 0014. |
| Replay-the-replay (using a replay session as the source) | Works.  The replay's `config.snapshot.yml` is self-contained. |
| Source session has a tool that doesn't exist anymore | Live re-execution fails on that tool; the replay surfaces it as a tool error; comparison labels divergence at that turn. |

---

## Observability

Each replay target writes its own session dir, so all existing
observability (events.jsonl, session.log, the TUI in-tree) just works
per-target.

Two new event types added to `events.py`:

- `session.aborted` — payload `{reason, details}` where `reason` is
  one of `"cost_cap"`, `"user_cancelled"`, `"provider_error"`.
  Emitted before `session.ended` when the session ends abnormally.
- `replay.target_completed` — payload `{source_session_id,
  target_session_id, provider, model, cost_usd, wallclock_seconds}`.
  Emitted at end of each replay target so the batch driver can build
  its summary without re-parsing events.jsonl.

Both get a one-line formatter in `log_writer/formatter.py` so they
show up in `session.log`.

---

## File layout

```
src/arc/replay/override.py
src/arc/replay/batch.py
src/arc/replay/compare.py
src/arc/plugins/max_cost/
  __init__.py
  manifest.yml
  plugin.py
src/arc/tui/replay_menu.py
src/arc/tui/compare_render.py
src/arc/cli.py                       ← +`compare` subcommand, +menu trigger for `arc replay`
src/arc/runtime/events.py            ← +SESSION_ABORTED, +REPLAY_TARGET_COMPLETED
src/arc/plugins/log_writer/formatter.py  ← +entries for the two new event types
tests/unit/test_replay_override.py
tests/unit/test_max_cost_plugin.py
tests/unit/test_replay_batch.py
tests/unit/test_compare_render.py
tests/integration/test_cross_provider_replay.py
```

No new deps.  Uses existing `prompt_toolkit` (dialogs), `rich` (tables,
Live view), `subprocess` (batch fan-out), `httpx` (already pulled in).

---

## Test plan

> Unit tests run anywhere with mocked providers + pricing.  Integration
> test requires at least two real providers (one cloud, one local) to
> exercise the full cross-provider path; gated by API key presence.

Unit (`test_replay_override.py`):
1. `apply_override` swaps provider name + model in a Config
2. Override defaults api_key_env from the catalog for each known provider
3. Override defaults base_url for local providers
4. Replay engine constructs the overridden provider via `build()` and
   makes one round-trip call (mocked provider) on a single-turn source

Unit (`test_max_cost_plugin.py`):
1. Plugin accumulates input+output costs across multiple turns
2. Unknown pricing rate → no enforcement, no crash
3. Cap exceeded → raises `ReplayCostExceeded` with the running total
4. Local provider (zero rate) → never triggers cap
5. Cap of 0.0 → first non-zero call triggers

Unit (`test_replay_batch.py`):
1. Parser: `"a:b,c:d"` → `[("a", "b"), ("c", "d")]`
2. Parser: malformed pair → clear error naming the bad fragment
3. Scheduler: cloud + ollama targets get parallel handles; llama_cpp
   targets are serialized
4. Scheduler: a failing target doesn't block siblings
5. BatchResult includes session_id, return_code, summary metrics

Unit (`test_compare_render.py`):
1. Summary table renders 2 sessions correctly (counts, costs, wall time)
2. Summary table handles N>2 (no turn-by-turn, just metrics)
3. Turn-by-turn diff aligns by turn index, handles asymmetric turn counts
4. Aborted-by-cost session renders an "aborted" badge
5. Final-response truncation preserves the first 80 chars

Integration (`test_cross_provider_replay.py`):
1. Skip unless both an API key (Anthropic) and `OLLAMA_HOST` are set
2. Record a 2-turn session against Anthropic
3. Replay it against `--override-provider ollama --override-model
   llama3.1:8b`; assert new session dir, assert events.jsonl present,
   assert at least one llm.call.completed event
4. Replay with `--max-cost-usd 0.0001` against a cloud target → aborts
   with `session.aborted` event in events.jsonl

Smoke:
- Record a real session with a non-trivial task (5+ turns, 2+ tool calls)
- `arc replay` (no args), walk the menu, pick the same model →
  deterministic replay, confirm zero divergence
- `arc replay`, pick an override provider → live re-run, confirm it
  runs end-to-end
- `arc replay`, batch against 2 local + 1 cloud → comparison view
  shows three columns
- `arc compare <orig> <replay>` from the CLI → same comparison view
  as the post-batch auto-render

---

## Open questions

1. **Should the TUI launch the comparison view automatically at end of
   batch, or just print the new session ids and let the user run
   `arc compare`?**
   Resolution: launch automatically.  The whole point is the
   comparison; making the user type another command is friction.

2. **What if the source session was paused/branched?  Replaying from
   "beginning" might miss the branch context.**
   Resolution: the menu shows the branch lineage in the session
   picker (today's `arc sessions` output already does this).  Replay
   uses the events.jsonl in the chosen session dir, period — branches
   are first-class sessions with their own dirs.

3. **Should the cost cap include the original session's recorded cost,
   or just the replay's cost?**
   Resolution: just the replay's cost.  The original is a sunk
   cost; the cap is about bounding the new spend.

4. **Should `arc compare` work on non-replay sessions?** (e.g., compare
   two unrelated sessions that happen to be about similar tasks.)
   Resolution: yes, it operates on session dirs without caring
   whether one was a replay of the other.  Comparison is purely
   metric-driven.

5. **Tool-call divergence: do we surface it as an "interesting" event
   in the comparison, or just show the asymmetric tool-call counts?**
   Resolution: just show counts and the per-turn breakdown for now.
   A dedicated "divergence point" detector is a follow-up if it
   becomes useful.

---

## State

Landed.

---

## Implementation notes

1. **`--override-provider` implies `--live-llm`.**  Designed as
   independent flags but in practice an override without live-LLM is
   semantically incoherent (the deterministic replay just re-emits the
   recorded responses — the override has no effect).  The CLI silently
   upgrades to `--live-llm` when override is specified.  Documented in
   `_cmd_replay` and visible in the printed mode label.

2. **Batch driver uses `subprocess.Popen`, not in-process threads.**
   Each replay target runs in its own arc CLI invocation so the
   per-session writes to events.jsonl + meta.json go through the
   identical code path as a single-target replay.  This means no
   special "batch mode" plumbing in the loop or the recorder — every
   resulting session is just a normal recorded session.  Trade-off:
   startup cost per target (~1s of Python import + bootstrap) is
   nontrivial; matters less than correctness for now.

3. **Stdout parsing for child session id.**  The batch driver
   extracts each child's new session id by parsing the `replaying
   <src> → <new>` line in stdout.  Brittle if the message format ever
   changes — pinned in `test_replay_batch.py::
   test_session_id_extraction_*`.  If/when this needs to be more
   robust, switch to a `--print-session-id-to <fd>` flag the child
   honors.

4. **`max_cost` plugin builder lives in `arc.plugins.__init__` like
   the others.**  It's registered as `"max-cost"` in `_BUILDERS` so it
   can be enabled via `config.yml` for non-replay sessions too.  But
   the replay path bypasses the builder and constructs it directly
   (`MaxCostPlugin(...) + registry.register(...)`) because the cap is
   a per-invocation flag.  Both paths share the same plugin class.

5. **`session.aborted` event emission requires bus access.**  The
   plugin holds an optional bus reference via `bind_bus()`.  Without
   it, the abort still works (raise propagates, session ends) but the
   `session.aborted` event isn't recorded.  For the replay CLI path,
   the bus is bound during `_cmd_replay`; for config-file-enabled
   `max-cost` usage, the builder binds the bus from `PluginBuildContext`.

6. **`replay/compare.py` reuses the existing event taxonomy.**  No
   need to introduce comparison-specific event types — the existing
   `turn.started`, `turn.ended`, `tool.call.started`,
   `llm.call.completed`, `session.aborted` are sufficient to compute
   every summary metric.  This means historical sessions recorded
   before 0019 also compare cleanly (modulo `session.aborted` only
   appearing in newer sessions).

7. **`/replay` slash command spawns a subprocess.**  Trying to
   piggyback the existing TUI's prompt_toolkit application would
   require pausing/resuming the alt-screen render — messy.  Instead,
   `/replay` invokes `python -m arc.cli replay` as a subprocess; the
   menu owns the terminal for its lifetime; on exit, the parent TUI
   redraws.  Same pattern would work for any future heavyweight TUI
   subscreen.

8. **Three-or-more-session compare skips turn-by-turn diff.**  Rich
   tables get hard to read with N>2 columns and the turn-by-turn diff
   becomes an N-way mess.  Summary table only is the sensible default;
   `arc compare a b` for the rich two-way view.

9. **Wallclock seconds uses `session.started` ts → last-event ts.**
   Not strictly equivalent to wall time spent waiting on the model
   (the last events include the recorder's own writes), but close
   enough for the comparison view to surface "ollama ran in half the
   time of Claude" signal.

10. **Cost cap of `0.0001` works.**  The integration test in the
    design doc uses `--max-cost-usd 0.0001` to verify abort behavior
    on a cloud provider.  The plugin uses a strict `>` comparison so
    the first non-zero call trips it.

A sensible order: land 0017 → 0018 → 0019.  Or land 0019's
CLI-only path first (Ollama + cloud, no menu), then layer the menu on
top after 0017 ships.
