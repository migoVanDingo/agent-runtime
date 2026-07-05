# Roadmap — how to make the arc ecosystem truly great

*Forward-looking. Ordered by leverage: what most increases arc's value per unit
effort, biased toward the two things a hosted competitor can't match —
observability/replay and provider-independence.*

---

## Tier 0 — finish what's started (small, high-signal)

1. **Wire real sub-agent cost.** `subagent.returned.cost_usd` is still the 0020
   placeholder (`0.0`). The `PricingTable` exists; thread it through the child
   metrics observer so dispatches show real numbers. Without it, the cost story
   (a differentiator) has a visible hole.
2. **Decompose `cli.py` (1818 lines).** It is the single worst file in the tree
   and the biggest onboarding tax. Split into `cli/` with one module per command
   group (sessions, replay, mcp, plugins, subagents, llm, setup). Pure
   mechanical, high readability payoff.
3. **Close the `bash_exec` docker escape (now safe).** The container sub-agent
   no longer needs raw `docker` (it has `image_build`/`gc`). Add `\bdocker\b`
   to the guard so the main agent can't bypass the verifying sub-agent via the
   shell. Gate on `inside_subagent()` if you ever want the child to keep it.
4. **Default resource caps in cos.** `limits` is optional today, so an agent can
   OOM/fork-bomb the host. Ship a default cpu/mem/pids cap that a spec can raise,
   not silently unlimited. (See security audit.)

## Tier 1 — make the differentiators undeniable

5. **A replay/branch UI.** The five modes are the killer feature but live behind
   CLI subcommands. A visual timeline — turns as nodes, branches as forks, click
   to time-travel — turns "we log everything" into "watch me rewind this agent."
   This is the demo that sells arc over a black-box CLI.
6. **Run diffing.** `arc diff <run-a> <run-b>` — same inputs, two providers or
   two prompts, show where they diverged (tool calls, tokens, outcome). Trivial
   on top of `events.jsonl`; nothing hosted can do it because they don't give you
   the log.
7. **Cost/telemetry dashboard from events.** Per-session and per-sub-agent token
   + $ rollups, provider mix, tool-call histograms — all derivable from events,
   surfaced in `arc show`/TUI. (Memory notes a deferred 0080/0087 telemetry line;
   this is that.)
8. **A "hardened" sandbox profile in cos.** Opt-in cap-drop + read-only rootfs +
   seccomp for untrusted binaries (the RE use case runs untrusted code!). Today
   only `network=none` + limits. This is the difference between "runs my toy
   webserver" and "safe to detonate malware in."

## Tier 2 — grow the ecosystem

9. **A Triton dynamic-DSE sub-agent** to complement angr (static). The seam
   already anticipates it (arc-plugin-angr was named by engine, not abstraction).
   Containerized via cos job-dispatch → dodges native-install pain entirely.
10. **The job-dispatch path in cos (design 0024 v2).** Right now cos runs
    containers imperatively. The vision — an engine plugin authors a structured
    `SolveRequest`, cos runs the engine image with the spec injected — is the
    thing that makes angr/Triton/radare "just work" cross-platform. Build the
    dispatch primitive and one engine on top of it.
11. **A registry/index of sub-agents & plugins.** As the set grows, a
    `arc plugins search` / catalog (even a static index repo) makes the ecosystem
    discoverable. The entry-point discovery already gives you the mechanism.
12. **Persistent cross-session memory as a plugin.** A `memory` plugin
    (session-scoped hooks + a small store) that survives across sessions — the RE
    workflow wants "what did I learn about this binary last week." Fits the plugin
    model cleanly; no runtime change.

## Tier 3 — platform maturity

13. **Multi-agent collaboration primitive.** Sub-agents are depth-1 and
    isolated. A supervised "team" pattern (a planner sub-agent that fans out to
    workers, results merged) would need the recursion prohibition relaxed to a
    bounded depth with a budget — design carefully, it is where most agent
    frameworks accrete complexity and bugs.
14. **A minimal auth/identity layer for cos** if it is ever exposed beyond
    loopback (a lab box on a LAN). Today the trust model is "any local process";
    a token + bind-address config would make LAN use defensible.
15. **Snapshot/export of a run as a shareable artifact.** `arc export <id>` →
    a self-contained bundle (events + config + meta) someone else can `arc
    replay`. Turns a debugging session into a reproducible report — huge for RE
    write-ups.

---

## The through-line

arc's moat is **the run is the artifact**: you own the event log, so you can
replay, branch, diff, audit, and export in ways a hosted agent structurally
cannot. Every Tier-1 item above is a way to make that moat visible and usable.
Provider-independence is the second moat — Tier-2/3 keep local models and
containerized engines first-class. Build toward those two, and arc has a reason
to exist that no amount of polish on a hosted competitor can erase.
