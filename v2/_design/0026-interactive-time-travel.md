# 0026 — Interactive time travel (`/rewind`, `/retry`, `/model`)

## Status: IMPLEMENTED (2026-07-06, feature/0026-time-travel — phases a–e).
## Companion to 0027 (visual timeline, not yet built).

## Motivation

arc's branch/resume machinery (modes 1 and 4, 0006/0007) is complete but
invisible: it lives behind `arc resume <id> --at-turn N`, which means
time-travel requires quitting the TUI, finding the session id, counting
turns, and relaunching. Nobody does that mid-thought.

Everything the interactive version needs already exists:

- `resume.messages_from_events(events, max_turns=N)` reconstructs a
  well-formed conversation truncated at any turn boundary.
- `cli.wiring.build_session(..., initial_messages=...)` wires a new session
  seeded with a prior conversation.
- `meta.json` lineage stamps (`resumed_from`, `branched_at_turn`) record the
  fork for the timeline (0027).
- `arc resume` already loads the **current** config rather than the source
  session's snapshot — so "continue this conversation on a different model"
  is already the semantics of resume. It's just buried.

This design adds the TUI front end: rewind to any prior turn and branch with
a new (or the same) prompt, and swap the provider/model mid-conversation —
without leaving the session loop.

Concretely enabled workflows:

- **Rephrase**: rewind one turn, ask the question better.
- **Sample again**: `/retry` — same prompt, fresh roll of the dice.
- **Distillation / model relay**: plan with a strong model, `/model` to a
  cheap one, continue with the plan in context. Or the reverse: triage with
  a cheap model, escalate the hard turn.
- **A/B at the point of divergence**: rewind to turn N and try approach B;
  the timeline then shows both branches forking from the same node.

## The core primitive: session rebuild

Every feature in this doc is one operation with two knobs:

> **Rebuild**: build a new session seeded with
> `messages_from_events(current events.jsonl, max_turns=N)` and an optional
> provider override; re-register the TUI as an event hook; start it; stamp
> lineage meta on the new session. The TUI process never exits. The parent
> session either ends (pre-tabs) or stays live in a background tab (see
> Tabs) — the recorder writes per-event, so its recording is complete
> through the last `turn.ended` either way and can be truncated from while
> live.

- `/rewind N` = rebuild with `max_turns=N`, same provider.
- `/retry`    = rebuild with `max_turns=tip-1`, same provider, auto-resend
  the recorded user input of the dropped turn.
- `/model X`  = rebuild with `max_turns=tip` (full conversation), provider
  override X.

### Branch = new session, not in-place truncation

`/clear` already mutates `_session._messages` in place, so in-place rewind
is tempting. Rejected:

- Events are append-only and are the source of truth. A rewind marker inside
  one events.jsonl would force every consumer (replay modes 2/3, resume,
  log_writer, compare) to learn rewind semantics. A new session dir keeps
  every recording linear and every existing consumer untouched.
- Lineage. `resumed_from` + `branched_at_turn` on a fresh session is exactly
  what mode 4 writes today and exactly what the 0027 timeline renders as a
  fork. In-place rewind would erase the abandoned branch — the opposite of
  what this feature is for.

The abandoned turns stay fully replayable in the parent session. Nothing is
ever lost — that's the point.

### Branch-on-submit (no empty sessions)

Entering rewind mode and moving the cursor is **UI-only** — no session is
created until the user actually submits a prompt at the rewound position.
Esc/abandon costs nothing and creates nothing. This avoids littering the
sessions dir with zero-turn branches (which would pollute the timeline).

Consequence: the rebuild happens at prompt-submit time, between turns, at
the top of the TUI's prompt loop. There is never in-flight state (LLM call,
tool call, sub-agent dispatch) to worry about — the same invariant pause
relies on.

### Truncation source: events.jsonl, not in-memory messages

The in-memory `_session._messages` list has no turn markers; events.jsonl
does (`turn.ended`). Reusing `messages_from_events` on the live session's
own recording (the recorder plugin has already flushed it — it writes
per-event) gives:

- turn-boundary correctness identical to `arc resume --at-turn`,
- one tested code path instead of two,
- correct handling of denied/failed tool calls (already encoded there).

Edge: sessions with `sliding_window_context` enabled may have an in-memory
message list that differs from the full recording (packed context). Rebuild
from the recording is *more* correct — the new branch starts from the true
history and the context plugin re-packs as needed.

## Commands

### `/rewind [N]` — rewind mode

`/rewind` enters a dedicated input mode: its own small `PromptSession` with
its own key bindings (the 0019 replay-menu pattern), so nothing leaks into
the main prompt's history or typing behavior.

```
⏪ rewind — ←/→ step turns · Enter/type selects · Esc back to tip
┌ turn 6/7 ── you: check the strings output for the password…
└ arc: the strings dump has three candidate literals: "s3cr3t_…
```

- **`←` steps one turn back, `→` one forward.** Each step **prints the turn
  card you land on** (truncated user input + assistant answer). The inline
  TUI is append-only, so stepping back literally re-reads the conversation
  in reverse — navigation and "scrolling to find the spot" are the same
  gesture. The toolbar mirrors position (`⏪ turn 3/7`).
- **Enter selects the current turn.** (Type-to-select was considered and
  dropped: exiting the mode on a printable key would swallow that first
  character of the prompt.) The mode exits and the prompt becomes the
  branch point:

```
arc(3)❯ actually, disassemble it with radare2 instead
⑂ branched SES01ABC… @ turn 3 → SES01XYZ…  (12 messages restored)
```

- **Esc cancels** to the tip; no session is created (branch-on-submit
  unchanged — selecting a turn still creates nothing until a prompt is
  submitted).
- **`/rewind N`** skips the walk for users who already know the target
  (e.g. reading the 0027 timeline in a browser next to the terminal).
- N is clamped to `count_completed_turns` exactly like `--at-turn`.
  `/rewind 0` = branch from nothing (fresh conversation, lineage recorded) —
  allowed for symmetry with `--at-turn 0`.

**Why ←/→ and not ↑/↓:** up/down is prompt-history muscle memory everywhere
else in the TUI, and the 0027 timeline draws time left→right — `←` means
"back in time" in both views. Inside the dedicated PromptSession either
binding is technically free; the choice is coherence.

### `/retry`

Sugar: rewind exactly one turn and auto-resend that turn's recorded user
input verbatim. The echoed prompt renders normally so the scrollback reads
as a fresh ask. Meta additionally records `retry_of_turn: N` so the timeline
can label the edge "retry" instead of "branch".

### `/model [provider/model]`

- `/model` (no arg): print current provider/model and the configured
  providers available to switch to.
- `/model anthropic/claude-sonnet-5` (or just a model name if the provider
  is unambiguous): rebuild at the tip with a provider override.

```
⑇ model swap: gemini/gemini-2.5-pro → anthropic/claude-sonnet-5
⑇ continued as SES01XYZ…  (14 messages restored)
```

- Composes with `/rewind`: rewind first, then `/model`, then prompt — the
  branch runs on the new model from turn N.
- The override is **session-scoped and in-memory**. config.yml is not
  rewritten; the next `arc` launch uses the file config. (Persisting is
  `arc setup`'s job.)

### `/replay` naming

`/replay` already exists (drops into the 0019 replay-menu subprocess) and
modes 2/3 are a different feature (verification, not conversation). The new
commands deliberately avoid the word "replay". `/help` gets a "time travel"
section explaining the distinction.

## Tabs: parent and branch, side by side

Branching raises the question of what happens to the parent. Two models,
shipped in that order:

**Pre-tabs (phases a–c): branch replaces.** The parent session ends cleanly
when the branch is created; the branch becomes the live session. Nothing is
lost — the parent is a complete recording, resumable anytime (`/sessions`,
the timeline, or `arc resume`). Killing the branch exits the TUI like any
session; the parent is unaffected because it already ended cleanly.

**With tabs (phase d): branch opens a tab.** The parent stays live in a
background tab; the branch opens focused. Toggle freely between them.

Why tabs are cheap here: the TUI is single-threaded and runs one turn at a
time, so a background tab is a **live-but-idle** session parked between
turns — no in-flight LLM/tool/sub-agent state, no events flowing (the same
between-turns invariant pause relies on). Tabs are therefore an ownership
change in `TUIApp` (N sessions instead of one, prompt loop aimed at the
focused tab), not a concurrency feature.

Why tabs at all (vs "just resume the parent when you want it back"):
resume always mints a new session id, so toggling parent↔branch without
tabs creates a resume-session per switch — session-dir churn and dotted
edge chains in the timeline. Tabs keep both original sessions live, so
toggling creates nothing.

Semantics:

- **Tab = live AgentSession.** The toolbar grows a tab strip:
  `1:SES…4NGN │ 2:⑂3 SES…ZQ8*` (`⑂3` = branched at turn 3, `*` = focused).
- **Switch**: `/tab` lists tabs; `/tab N` focuses one; alt+1…9 where the
  terminal delivers it. Switching prints a divider + a compact re-render of
  the target's last few turns (append-only scrollback; same rendering as
  the rewind turn cards).
- **Close**: `/exit` (or Ctrl+D) closes the **focused tab** — that session
  ends; focus moves to the most recently used remaining tab. Closing the
  last tab exits the TUI. `/quit` closes all tabs and exits. So: killing
  the branch tab returns you to the parent tab, still live, exactly where
  it was.
- **Cap**: `tui.tabs.max` (default 4). Each live tab holds its own plugin
  instances, recorder handle, MCP server connections (stdio servers are
  real child processes) and sub-agent registry — the cap is a resource
  honesty knob, not a UI limit. At the cap, branching prompts to close a
  tab first.
- **Events**: TUIApp registers `on_event` on every tab's registry;
  background tabs are idle so only the focused tab ever emits. Turn/token
  toolbar counters become per-tab state.
- **SIGINT**: unchanged — it targets the focused tab's in-flight turn
  (pause), since background tabs have nothing in flight.
- Rewind composes: `/rewind` in any tab branches *that tab's* session into
  a new tab.

## Config snapshot correctness (the subtle requirement)

Replay reconstructs a session from its `config.snapshot.yml` — the snapshot
must describe the config the session **actually ran with**. Today
`build_session` snapshots `paths.config_file.read_text()`, which is correct
because the file is the config. With `/model`, the effective config diverges
from the file.

Fix: rebuild passes the **effective** config into the snapshot —
`PluginBuildContext.config_snapshot_yaml` gets the file text with the
provider block replaced by the override (serialize the effective provider
section; the comment-preserving writer in `arc/setup/writer.py` already
knows how to do targeted section rewrites). A branched session must replay
on the model it actually used, not the one in the file at the time.

`meta.json` additionally records the swap explicitly:

```json
{
  "resumed_from": "SES01ABC…",
  "branched_at_turn": 3,
  "provider_override": {"name": "anthropic", "model": "claude-sonnet-5"},
  "retry_of_turn": 4
}
```

(`provider_override` / `retry_of_turn` only when applicable.)

## Rebuild mechanics in TUIApp

`TUIApp` currently owns one `AgentSession` for its whole `run()`. The
rebuild sequence, extracted as `TUIApp._rebuild_session(max_turns, provider_override)`:

1. `self._session.end()` — recorder finalizes events.jsonl + meta.json.
2. Read the *just-finalized* recording; `messages_from_events(..., max_turns=N)`.
3. Resolve provider: existing instance if no override, else
   `build_provider(effective_cfg.provider)`.
4. `build_session(effective_cfg, paths, provider=…, tools=…,
   subagent_registry=…, gate=…, initial_messages=…)` — same wiring as
   resume, including the guard-gate upgrade to `TUIGate`.
5. Re-register the TUIApp on the **new** registry (`on_event` at 200) —
   same registration `run()` does at startup.
6. `session.start()`; emit `session.branched` (+ `provider.swapped`) — the
   **authoritative** lineage record, persisted to events.jsonl immediately.
7. Stamp lineage meta **eagerly** via `stamp_session_meta` (shared helper in
   `cli/wiring.py`; child's `on_session_start` has already created meta.json,
   so the merge-on-disk lands). Because tabs keep a branch open indefinitely,
   the pre-tabs "stamp only after end" approach would lose lineage on a hard
   kill; eager stamping keeps the derived meta honest while the tab is live.
   The recorder rewrites meta (without lineage) at its eventual
   `on_session_end`, so `self._pending_meta` re-stamps after end too
   (`_end_session_and_stamp`). Events remain the source of truth — 0027's
   scanner reads `session.branched`, treating the meta stamp as a fast-path.
8. Print a fresh banner line with the new session id + lineage note. (Tab
   counters live on the `_Tab`, so no per-session counter reset is needed —
   a fresh tab starts them at zero.)

Notes:

- Tools and the sub-agent registry are rebuilt with the session (step 4),
  not reused — MCP server connections and plugin session state
  (`on_session_start`) are session-scoped by contract; carrying instances
  across sessions would violate the plugin lifecycle. This makes rebuild
  cost ≈ session startup cost (fine; it's a deliberate user action).
- SIGINT/pause handler: the handler resolves the pause plugin through
  `self._session` — it must not cache the session object. Audit
  `_install_pause_on_sigint` and `_find_pause_plugin` for staleness; make
  them read `self._session` at fire time.
- `_find_sessions_dir` walks the registry for the recorder — works
  unchanged post-rebuild since the new registry has a new recorder.

## Events

New `EventType` entries (+ `log_writer/formatter.py` dispatch, per
convention):

- `session.branched` — emitted in the **new** session at start:
  `{source_session_id, branched_at_turn, restored_message_count}`.
- `provider.swapped` — emitted in the new session when `/model` was used:
  `{from_provider, from_model, to_provider, to_model}`.

The parent session needs no new events — it simply ends. Its `turn.ended`
count tells the timeline where the fork attaches.

## Cross-provider carry-over

Messages are provider-neutral `ContentBlock`s and 0010/0019 already handle
cross-provider translation for resume/replay, including thinking blocks and
Gemini schema sanitization. `/model` leans on exactly that path. Test matrix
(integration, auto-skip without keys): gemini→anthropic and anthropic→gemini
branches where the parent conversation contains (a) thinking blocks,
(b) tool calls with results, (c) a denied tool call.

## Testing

- Unit: fake `prompt_fn` driving `/rewind 2` → assert new session dir,
  lineage meta, message count, TUI re-registration, counter reset. `/retry`
  resends verbatim. `/model` snapshot contains the override. Cancel path
  creates nothing.
- Unit: `_rebuild_session` truncation equals `messages_from_events` on the
  same recording (property: rebuild(N) == resume --at-turn N).
- Integration: the cross-provider matrix above.
- Replay regression: a branched-with-override session must `arc replay`
  cleanly from its own snapshot (this is the config-snapshot requirement,
  enforced by test).

## Phases

- **a.** Shared meta-stamp helper in `cli/wiring.py`; `_rebuild_session`
  seam + `/rewind N` (direct form) + `/retry` + events + formatter entries.
  Branch-replaces semantics.
- **b.** Rewind mode: dedicated PromptSession, ←/→ turn walking with
  printed turn cards, Esc/Enter handling.
- **c.** `/model` + effective-config snapshot + cross-provider tests.
- **d.** Tabs: multi-session TUIApp, tab strip in toolbar, `/tab` +
  alt+1…9, per-tab counters, close/focus semantics, `tui.tabs.max`.
- **e.** Polish: `/help` time-travel section, tab-completion entries for
  the new commands.

0027 (visual timeline) consumes what this produces and ships second, but is
code-independent — only the meta contract above is shared.
