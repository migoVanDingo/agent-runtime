# What's good — strengths & why arc over Claude Code

*An honest assessment. arc is a personal, self-hosted agent runtime built for a
reverse-engineering workflow. It is not trying to be Claude Code; where it wins,
it wins on things Claude Code structurally cannot or will not do.*

---

## The strengths, ranked by how much they matter

### 1. Total observability + byte-faithful replay — the killer feature
Every observable moment is a `RuntimeEvent` written to `events.jsonl`, and every
`LLMResponse` carries `.raw` (the provider's verbatim response). From that one
log arc reconstructs the entire run five ways: deterministic replay, live-LLM
replay, time-travel resume, branch, rerun. **Nothing else in the ecosystem is
authoritative** — the human log, meta files, cost, and the TUI all derive from
events.

Why it matters: you can rewind an agent to turn 3, change one thing, and branch
a new timeline *without paying for the first three turns again*. You can diff two
runs. You can audit exactly what the model saw and did, byte for byte. This is
the feature to build the product around.

### 2. Genuinely pluggable, with a frozen contract
Three narrow seams — `plugin_api` (v0.1), `subagent_api` (v0.2), and MCP — each
versioned and documented, each fork-a-template-and-ship. An external plugin is a
pip package with one entry-point line. The runtime discovers it, prompts once,
persists the choice. The contract is small enough that the whole plugin surface
fits in one file (`plugin_api.py`).

Why it matters: you extended the system four times during this project (ghidra,
angr, gcs, cos-via-MCP) without touching the runtime once. That is the test of a
real plugin architecture, and it passed.

### 3. Provider independence, including local models
Providers are Layer 3 behind a Protocol: Gemini, Anthropic, Vertex, Ollama,
llama.cpp. Sub-agents pick their *own* provider — and any of them can be
repointed at a local GPU box via a config override with no code change. The
video sub-agent runs on Vertex for native video ingest; the container sub-agent
runs on cheap Flash and will run on your lab Ollama.

Why it matters: Claude Code is Anthropic-only by design. arc treats the model as
swappable infrastructure. For a homelab with a "beast GPU," that is the whole
game.

### 4. Sub-agents with real context isolation + methodology
A sub-agent is a scoped child session with its own provider, its own tight tool
allowlist, its own system-prompt methodology, and — critically — its own
context that never pollutes the parent. The parent gets a structured result, not
a 22-tool-call transcript. Dispatch guards (quota, consecutive-failure circuit,
watchdog timeout) keep a confused parent from fork-bombing them.

### 5. Reverse-engineering as a first-class use case
Live Ghidra control (rename, decompile, xrefs, strings via a Java extension over
HTTP), symbolic execution behind a seam (angr, containerized to dodge the macOS
wheel problem), long-shell-output tolerance, persistent state. This is a
workbench built for one demanding workflow, not a general assistant.

### 6. Policy lives in plugins, not the runtime
The `guard` plugin's `delegate_only_tools` rule — "container work must go through
the verifying sub-agent" — is 40 lines in a plugin, gated on `inside_subagent()`,
and fails open when the owner is absent. The runtime has no opinion about
containers; the policy is composable and removable. That is the design principle
paying rent.

### 7. The environment is a dependency (cos / job-dispatch)
cos turns "the environment is a dependency" into architecture: ship a recipe (an
image), dispatch a job into a container, with labels-as-state and no sidecar DB.
It is harness-agnostic — arc consumes it over MCP, but so could anything.

---

## Why arc over Claude Code — the honest version

**Use arc when you want:**

| You want… | arc | Claude Code |
|---|---|---|
| To own the event log and replay/branch/audit any run | ✅ signature feature | ❌ opaque |
| To swap providers / run local models on your own GPU | ✅ first-class | ❌ Anthropic-only |
| A homelab-owned, offline-capable agent | ✅ self-hosted | ❌ hosted/CLI |
| To pin a different model per sub-task | ✅ per-sub-agent provider | ⚠️ limited |
| Deep RE tooling (live Ghidra, angr, containerized engines) | ✅ purpose-built | ❌ not the target |
| To read the entire contract in one file and fork a plugin in an hour | ✅ tiny surface | ⚠️ larger, hosted |
| To orchestrate Docker with labels-as-state + verifying sub-agent | ✅ cos + guard | ❌ not built in |

**Be honest — use Claude Code when you want:** a polished, maintained,
hardened product with a large tool ecosystem, IDE integrations, managed updates,
and no operational burden. arc is a single-maintainer research runtime; Claude
Code is a shipping product with a team behind it. arc's TUI is inline
prompt_toolkit, not an IDE. arc has no auth, no multi-user story, and (see the
security audit) a Docker control plane you must not expose beyond loopback.

**The one-sentence pitch:** *arc is the agent runtime you use when the run itself
is the artifact — when you need to own, replay, branch, and audit every step,
run it against any model including your own, and extend it in an afternoon —
built for reverse engineering and homelab autonomy rather than as a hosted
product.*

That is a real, defensible niche. Lean into observability/replay and
provider-independence; those are the two things a hosted competitor cannot match
for a self-hoster, and they should be the headline of the README.
