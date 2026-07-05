# arc ecosystem — code review & security audit

*Conducted 2026-07-04. Read-only analysis + documentation. No bug fixes, no
feature code, no commits (per brief). v1 excluded (deprecated).*

---

## Scope

| Component | What | LOC (approx) |
|---|---|---|
| **v2** | the arc agent runtime (runtime, providers, plugins framework, MCP, sub-agents, TUI, setup, replay, CLI) | ~19,600 |
| **6 plugins** | ghidra, angr, briefbot, websearch, gcs, template | ~9,000 |
| **3 sub-agents** | container, video, template | ~1,500 |
| **cos** | container-orchestration-service (Docker control plane, `~/Developer/`) | ~1,800 |

## Method

Four parallel deep-dive analyses (runtime core; plugins+MCP+subagent-runtime;
external plugins; cos+subagents), each an adversarial read returning findings
with `file:line`, severity, and concrete failure scenarios. Findings were then
verified against the source and synthesized here. Line numbers were current at
review time.

## The documents

| Doc | What's in it |
|---|---|
| **00-README.md** (this) | scope, method, executive summary, severity roll-up |
| **01-architecture.md** | how the whole thing works — layer/loop/plugin/subagent/MCP/cos diagrams + an end-to-end trace |
| **02-security-audit.md** | every security finding, ranked Critical→Low, 5 cross-cutting themes, remediation order |
| **03-code-quality.md** | long files, smells, duplication, coupling, dead code |
| **04-strengths-and-differentiators.md** | what's good + an honest "why arc over Claude Code" |
| **05-roadmap-and-extensions.md** | how to make the ecosystem great, ranked by leverage |
| **06-changes-made.md** | the dead code I removed + docs I updated (the only edits made) |

---

## Executive summary

**The codebase is well above average for a single-maintainer project.** The
architecture is genuinely sound: clean three-layer separation, a small frozen
plugin contract that all five external plugins honor without reaching into
internals, event-sourced observability with byte-faithful replay, safe
deserialization, and policy pushed into plugins rather than baked into the
runtime. The plugin coupling discipline is excellent. Nothing here is structural
rot.

**The security picture is the story of a local single-user tool, not a hardened
product** — and that is fine *as long as it is understood and documented*. The
audit's central theme: several capabilities assume a trust boundary that doesn't
exist. The two that matter most:

1. **cos can root the host, and its README claimed it sandboxes untrusted
   binaries — it does not.** Unvalidated bind mounts (mount `docker.sock` or `/`)
   plus default container capabilities and optional resource limits mean the
   "sandbox" is porous, and the MCP control plane is unauthenticated on loopback.
   (I corrected the README/CLAUDE to state this honestly.)

2. **Delegating to a sub-agent removes the safety layer, not adds one.** Child
   sessions run with `plugins.enabled=[]`, so `guard`/`safety_gate` never fire
   inside a sub-agent — yet the guard's `delegate_only_tools` deliberately routes
   dangerous tools *into* sub-agents, and `container_expert` carries a raw host
   shell (`bash_exec`). The enforcement model we built is half-real until child
   sessions inherit policy. This is the most architecturally important finding.

The third cluster is **egress/write confinement** in the plugins:
`arc-plugin-websearch` has no SSRF protection (cloud-metadata reachable),
`gcs_download` is an unconfined host-file-write primitive. Both are the classic
"prompt-injected agent abuses a capability" risk.

**None of these are hard to fix** — the remediation order in `02` is six items,
most at a single seam (a shared SSRF validator, cos default hardening, child
policy inheritance). The debt in code quality is concentrated and mechanical:
`cli.py` (1818 lines, 5× duplicated wiring) and a couple of ~250-line god
methods.

---

## Severity roll-up

| Severity | Count | Where the worst ones live |
|---|---:|---|
| **Critical** | 6 | cos (bind mounts ✅, unauth MCP), websearch (SSRF ×3 ✅), gcs (arbitrary write) |
| **High** | 10 | sub-agent-unguarded ✅, container_expert host shell ⚠️, fail-open policy, cos no-caps ✅, ghidra unauth bridge, angr OOM ×2, websearch denylist ✅/size ✅ |
| **Medium** | 14 | provider-param bleed, tool-cap 400 bug ✅, TOCTOU, gc-removes-images ✅, `_find` scope ✅, replay `.raw` gaps, MCP collision/hang, budgets |
| **Low** | ~15 | log/error disclosure, hardcoded tunables, regex false-positives, dup helpers |

> **✅ = mitigated over two passes (2026-07-05), see `_mitigation/` 01–07.**
> Pass 1: C1 bind-mount deny-list, H4 hardening, M2 tool-cap, M4 gc-preserves-images,
> M5 `_find` scope. Pass 2: C3/C4/C5/H8/H9 websearch SSRF seam, H1 child guard,
> H2 docker-block (⚠️ partial — `bash_exec` still a shell). Next recommended:
> C6/H10 (gcs write confinement), H3 (fail-closed policy). C2/H5 (cos/ghidra
> auth) deprioritized by the owner.

*(Reminder: "Critical/High" here is calibrated to the local-tool threat model —
prompt injection, local unauthenticated services, and untrusted RE inputs — not
to a remote-attacker web-app model. See the framing note in `02`.)*

## Headline conclusions

- **What's great:** observability + replay/branch (the moat), provider
  independence incl. local models, a real plugin contract, RE-first tooling,
  policy-in-plugins. See `04` — these are the reasons to use arc over a hosted
  agent, and they should be the README headline.
- **What to fix first:** cos hardening (caps + mount validation + auth), the
  sub-agent policy gap, and the websearch SSRF seam. See `02` remediation order.
- **What to pay down:** `cli.py`, the provider retry/tool-shape duplication, and
  the couple of god methods. See `03`.
- **Where to go:** wire real sub-agent cost, build a replay/branch UI and run
  diffing, ship a hardened cos profile, grow the containerized-engine story
  (Triton, job-dispatch). See `05`.

The system is a strong foundation with a clear, defensible identity. The gaps are
known, bounded, and mostly closable at single seams — the audit's job was to name
them precisely, which the numbered findings in `02`/`03` do.
