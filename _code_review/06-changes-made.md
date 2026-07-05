# Changes made during this review

*Per the brief: no bug fixes, no feature code, no commits. Only (a) safe dead-code
removal I am 100% confident about, and (b) documentation updates/pruning. Every
change is logged here.*

---

## Dead code removed (unused imports only — 100% safe)

Each was verified unused by grep (the symbol appears only on its import line).
All are unused imports, the safest possible removal; nothing else was deleted.
The riskier "dead contract / unwired module" candidates were **flagged, not
removed** (see `03-code-quality.md` → Dead code → "Flagged, NOT removed").

| File | Removed | Verification |
|---|---|---|
| `v2/src/arc/runtime/subagents/runner.py` | `from dataclasses import replace as dc_replace` | `grep dc_replace` → import line only |
| `v2/src/arc/runtime/subagents/runner.py` | `from datetime import datetime, timezone` | no `datetime`/`timezone` use in file |
| `v2/src/arc/runtime/subagents/runner.py` | `Message` (kept `Cancelled`) from `arc.runtime.hooks` | `Cancelled` used; `Message` not |
| `v2/src/arc/plugins/safety_gate/plugin.py` | `DEFAULT_PATTERNS` (kept `Pattern`, `catalog_by_name`) | no `DEFAULT_PATTERNS` use in file |
| `arc-plugin-websearch/.../backends/brave.py` | `SearchBackend` (kept `SearchQuery`, `SearchResult`) | Protocol satisfied structurally; import-line only |
| `arc-plugin-websearch/.../backends/google_pse.py` | `SearchBackend` | same |
| `arc-plugin-websearch/.../backends/searxng.py` | `SearchBackend` | same |
| `arc-plugin-websearch/.../backends/ddg_html.py` | `SearchBackend` | same |

**Verification after removal:** `arc.runtime.subagents.runner` and
`arc.plugins.safety_gate.plugin` import cleanly; `test_subagent_runner.py` +
`test_guard.py` (32 tests) pass; all four websearch backend modules import
cleanly. (The websearch test *suite* couldn't run in the canonical venv because
`respx` isn't installed there — a pre-existing environment gap, unrelated to
these edits; the source imports were confirmed clean.)

### NOT removed (deliberately) — needs an owner decision
- `assess_step` hook + `Step`/`StepAssessment`/`AssessStep` (`v2/.../hooks.py`,
  `bus.py`) — a dead *contract* (never fired). Wiring vs. deleting is a
  contract-surface decision, not obvious junk.
- `arc-plugin-gcs/.../formatters.py` `DISPATCH` + 16 `fmt_*` — unwired, but the
  module docstring signals intended future auto-registration.
- `arc-plugin-gcs/.../client.py` `GCSClient.bucket()` — plausible convenience API.
- `subagents/errors.py` `SubAgentTimeoutError` — dead but part of the frozen
  `subagent_api` public surface → keep.
- `cos .../backend.py` `_combined_logs` (dup), `Handle.status` (constant).

---

## Documentation updated

### Security-correcting (flagged by the audit as misleading)
- **`container-orchestration-service/README.md`** — the "Why" section claimed cos
  sandboxes "untrusted binaries," which the audit shows is false (unvalidated
  mounts + default caps). Rewrote it and added a prominent **Security / trust
  model** section: unauthenticated loopback MCP, not-a-sandbox-today, what IS
  enforced.
- **`container-orchestration-service/CLAUDE.md`** — added a **Security posture**
  block listing the known gaps (unauth MCP, unvalidated mounts, no cap-drop/pids
  limit, `gc` removing reusable images, `_find` managed-scope) with the audit ref.
- **`arc-sub-agent-container/README.md`** — added an **Enforcement caveat**:
  child sessions run with `plugins.enabled=[]` so guard/safety_gate don't fire
  inside the sub-agent, and its allowlist includes `bash_exec` (raw host shell) —
  so "delegation" is only half a control today (audit H1/H2).
- **`arc-plugin-websearch/CLAUDE.md`** — added **Known SSRF gaps** (C3/C4/C5/H8/H9)
  with the one-seam fix.
- **`arc-plugin-gcs/CLAUDE.md`** — added **Known security gaps** (C6/H10/M11):
  unconfined `gcs_download`, new-file-download mislabeled as read, inert budget.

### Staleness / pruning
- **`v2/CLAUDE.md`** — header table was badly stale: "~6,900 lines" → ~19,600;
  "386 tests" → 768 unit; provider list `Gemini, Anthropic` → added Vertex
  Gemini, Ollama, llama.cpp; added Sub-agents + MCP rows.

*(Other READMEs/CLAUDEs were reviewed and found current — the plugin/sub-agent
docs are accurate to their code; the audit's coupling check confirmed the plugin
contract docs match reality.)*

---

## Not touched (per brief)
- No source bug was fixed — the tool-call-cap dangling-`tool_use` bug (M2), the
  `gc`-removes-images bug (M4), the SSRF gaps, the sub-agent policy gap, etc. are
  documented in `02-security-audit.md` for the owner to fix.
- No commits made.
- v1/ was not analyzed or modified.
