# Security audit

*Scope: v2 runtime + all plugins + all sub-agents + container-orchestration-service
(cos). v1 excluded. Findings are ranked Critical → Low and cross-referenced by
component. Line numbers were current at review time.*

> **Framing.** arc is a single-user, self-hosted, local tool operated by its
> owner. There is **no privilege boundary between the user and the agent by
> design** — the user chose to run an autonomous agent on their machine. That
> reframes most findings: the real threat model is not "a remote attacker" but
> **(a) the agent/LLM being tricked (prompt injection) into abusing a
> capability, (b) a local unauthenticated service being reached by another
> local process or a malicious web page, and (c) an untrusted binary/input the
> RE workflow deliberately handles.** Every Critical/High below is exploitable
> under one of those three.

> **✅ Mitigations applied (2026-07-05/06), three passes.** Fixed: **C1, C3, C4,
> C5, C6, H1, H3, H4, H8, H9, H10, M2, M4, M5** and partially **H2** — see the
> `_mitigation/` directory (files 01–10) and the `MITIGATED` tags inline. Still
> open: **C2/H5** (cos + ghidra unauth services — deprioritized by the owner),
> the **H2 residual** (replace the sub-agent's `bash_exec` with scoped tools),
> and the tail of Mediums (M1, M3, M6, M7, M8, M9, M10, M11).

---

## Cross-cutting themes (read these first)

### Theme 1 — Unauthenticated local services with root-equivalent reach
cos (Docker daemon, `127.0.0.1:8770`) and the Ghidra bridge (`127.0.0.1:8765`)
both expose **mutation with no auth and no Origin/Host validation**. Any local
process — or a malicious web page the user visits, via a `fetch()` to loopback
(CSRF) or DNS-rebinding — can drive them. cos's blast radius is **host root**
(the daemon is root-equivalent); Ghidra's is silent tampering of the live
analysis DB. This is the single most important boundary in the ecosystem.

### Theme 2 — The sub-agent is an unguarded execution context
The `guard`/`safety_gate` plugins run in the parent, but a sub-agent's child
session is built with `plugins.enabled=[]` (`runner.py`), so **no policy fires
inside a sub-agent**. Meanwhile the guard's `delegate_only_tools` deliberately
routes dangerous tools (`container_*`, and the recommended `docker` block) *into*
the sub-agent, and `container_expert` carries `bash_exec` (a raw host shell).
Net: **delegating a capability to a sub-agent removes the safety layer that
protected it in the parent.** This directly undercuts the enforcement model we
built. It is the most architecturally significant finding.

### Theme 3 — Egress / SSRF to internal targets
`arc-plugin-websearch` performs agent-controlled HTTP fetches with **no SSRF
protection** on `http_request`/`extract_html`, a bypassable denylist on
`read_url`, and unvalidated redirect following — so a prompt-injected agent can
read cloud metadata (`169.254.169.254`), loopback services, and RFC1918 hosts,
and stream the bodies to the model.

### Theme 4 — Arbitrary host-file write / weak path confinement
`gcs_download` writes any allowed-bucket object to any host path (`~/.ssh/…`,
LaunchAgents) with new-file writes un-gated. cos bind mounts accept any source
(`/`, `docker.sock`). angr/cos/gcs accept arbitrary host input paths with no
workspace confinement.

### Theme 5 — Missing resource bounds (host DoS)
cos containers run with default caps, no `pids_limit`, and **optional**
cpu/mem limits (unlimited by default) → fork bomb / OOM the host. angr runs
symbolic execution in-process with only a between-steps soft timeout → OOM the
arc session. websearch has no response-size cap → decompression bomb.

---

## Critical

| # | Component | `path:line` | Issue |
|---|---|---|---|
| C1 | cos | `core/spec.py:35-43`, `core/backend.py` `_create_kwargs` | **Unvalidated bind-mount source.** `Mount.validate()` checks only type + non-empty. `mounts=[{source:"/var/run/docker.sock",target:...}]` or `source:"/"` gives a "sandboxed" container the daemon socket / host FS → **host root**. Defeats every other cos control. — ✅ **MITIGATED, `_mitigation/05`** (deny-list; workspace allow-list still open). |
| C2 | cos | `mcp_server/server.py:52-55` | **Unauthenticated MCP server drives Docker.** No token, no Origin/Host check on the streamable-HTTP endpoint. Any local process (or a web page via DNS-rebind) can `container_run` with a docker.sock mount → host root. `owner` is a self-asserted label, not identity. |
| C3 | websearch | `tools/http_request.py:73-104` | **`http_request` has zero SSRF protection.** — ✅ **MITIGATED, `_mitigation/06`** (routes through `safe_request`/`validate_url`). |
| C4 | websearch | `tools/extract_html.py:72-82` | **`extract_html` fetches arbitrary URLs unvalidated.** — ✅ **MITIGATED, `_mitigation/06`**. |
| C5 | websearch | `http.py:35-36` | **Redirects followed, never re-validated.** — ✅ **MITIGATED, `_mitigation/06`** (auto-redirects off; each hop re-validated). Full IP-pinning still open. |
| C6 | gcs | `tools/file_ops.py:337-340` | **`gcs_download` = unconfined arbitrary host-file write.** — ✅ **MITIGATED, `_mitigation/09`** (confined to `download_dir`; absolute/`..` rejected). |

## High

| # | Component | `path:line` | Issue |
|---|---|---|---|
| H1 | subagents (runtime) | `runtime/subagents/runner.py` (child `plugins.enabled=[]`) | **Sub-agents run tools with no guard/safety_gate.** — ✅ **MITIGATED, `_mitigation/07`** (child inherits a hard-denylist guard). |
| H2 | sub-agent-container | `spec.py:60-62` | **`container_expert` allowlist includes `bash_exec` + `ls` (raw host shell).** — ⚠️ **PARTIALLY MITIGATED, `_mitigation/07`** (docker + destructive commands now blocked in the child; `bash_exec` is still a general shell — scoped-tool replacement deferred). |
| H3 | v2 runtime | `runtime/bus.py:129-131,172-173` | **Security plugins fail *open*.** A throwing `before_tool_call` is swallowed → executes; 3 throws quarantine the plugin → policy vanishes. — ✅ **MITIGATED, `_mitigation/10`** (before_tool_call fails closed; `critical` plugins never quarantined). |
| H4 | cos | `core/backend.py` `_create_kwargs` | **No cap-drop / `no-new-privileges` / `pids_limit`; limits optional.** Default-capability container with no pids cap runs a fork bomb; no mem cap → OOM the host. — ✅ **MITIGATED, `_mitigation/04`** (pids/no-new-privs/default caps shipped; `cap_drop`+ro-rootfs `hardened` profile still open). |
| H5 | ghidra | `ghidra-extension/…/BridgeServer.java:66,123-132` | **Unauthenticated bridge, no Origin/Host check.** Loopback-only (good) but any web page can `fetch()` `POST /rename_function` (CSRF) to mutate the live binary DB; DNS-rebind can read decompiled C. |
| H6 | angr | `engine.py:135-146` | **No memory bound, in-process.** Symbolic execution allocates GBs inside one `simgr.step()` → OOM kills the whole arc session. No subprocess isolation / RLIMIT. |
| H7 | angr | `engine.py:135-145,220-222` | **Budget is soft.** Wall-clock/steps checked only *between* steps; a single heavy `step()` or the post-loop `solver.eval`/`posix.dumps` concretization (no timeout) overshoots `max_seconds` arbitrarily. |
| H8 | websearch | `tools/read_url.py:16-18,73` | **Denylist is exact-string host match.** — ✅ **MITIGATED, `_mitigation/06`** (replaced with resolved-IP `ipaddress` test). |
| H9 | websearch | `http.py:32-38` | **No response-size cap** (gzip bomb → OOM). — ✅ **MITIGATED, `_mitigation/06`** (streamed 10 MiB cap on decoded bytes). |
| H10 | gcs | `tools/file_ops.py:364-375` | **New-file download mislabeled operation `"stat"` (a read op)** → never triggers the UserGate. — ✅ **MITIGATED, `_mitigation/09`** (now `download_new`, a gated mutation). |

## Medium

| # | Component | `path:line` | Issue |
|---|---|---|---|
| M1 | subagents | `runner.py:489-498` | Child inherits `parent.provider.params` wholesale even when `spec.provider` differs → parent's provider-scoped config/secrets bleed into a different provider's construction. Only inherit when providers match. |
| M2 | v2 runtime | `runtime/loop.py:471-478` | **Tool-call cap mid-batch leaves dangling `tool_use` blocks** with no matching `tool_result` → provider **400** on the next iteration, retried until the turn fails. — ✅ **MITIGATED, `_mitigation/03`** (synthetic skipped results emitted). |
| M3 | v2 runtime | `runtime/bus.py:105-131` + `loop.py:490-500` | **TOCTOU in `before_tool_call`.** A post-policy plugin (priority 50) can mutate `input["command"]` after guard (10)/safety_gate (20) approved it; the loop executes the mutated call without re-validation. |
| M4 | cos | `core/backend.py` `prune_images(only_unused=True)` | **`gc` deletes reusable build-once images.** An `image_build` tag with no *current* container is treated as unused and force-removed — contradicts build-once-run-many and the tool's "Safe" docstring. — ✅ **MITIGATED, `_mitigation/01`** (named images excluded from `gc`). |
| M5 | cos | `core/backend.py` `_find` | **`_find`/`_require` match `cos.name` only, not `cos.managed`.** `exec`/`logs`/`stop`/`rm` act on any container carrying a matching `cos.name` label, even one cos didn't create. — ✅ **MITIGATED, `_mitigation/02`** (filter now requires `cos.managed`). |
| M6 | cos | `core/backend.py` `build`/`build_image` | Build `context` is an unvalidated host path. `context="/"` tars the whole host FS as build context (DoS) and a supplied `dockerfile` `COPY` can pull any host file into an image. |
| M7 | v2 runtime | `llm/process.py:200-231` | `arc llm stop` SIGKILLs by PID with only existence check → after PID reuse, kills an unrelated process. Verify start-time/cmdline against `PidState`. |
| M8 | v2 runtime | `providers/openai_compat.py:351-368` | `.raw` fallback `{"_repr": repr(resp)}` is not JSON-reconstructable → **breaks byte-faithful replay** for compat/local servers. |
| M9 | subagents (runtime) | `mcp/adapter.py:19-22` + `plugins/__init__.py` merge | MCP tool-name sanitization can collapse two server tools to one arc name; the resulting `merge_plugin_tools` `ValueError` is **not** isolated → crashes session startup, defeating per-server isolation. |
| M10 | mcp | `mcp/manager.py:166-178` | A hung server's in-flight `call_tool` isn't cancelled (only the waiter is) → the actor loop wedges; subsequent calls each burn full timeout before quarantine. |
| M11 | gcs | `plugin.py:205-216` | Budget guard is inert by default (`session_budget` unset → all caps `None`). Advertised cost cap does nothing unless configured. |
| M12 | angr | `spec.py:50-52` | `Budget.validate()` bounds only `max_seconds`; `max_steps`/`max_states` accept `1e12`, defeating two of three brakes. |
| M13 | angr | `engine.py:65-83` | Arbitrary host binary path into `angr.Project` (CLE parsers on attacker bytes), no workspace confinement. Mitigated by `auto_load_libs=False`. |
| M14 | briefbot | `dal.py:164,192-195` | `get_top_topics` orders by `last_seen_at`, absent from `REQUIRED_TOPIC_COLUMNS`, so schema-quarantine passes then the tool throws raw `sqlite3.OperationalError` mid-turn. |

## Low (abbreviated)

- **cos** `spec.py:105` provision steps line-injected into a synthesized Dockerfile (newline → extra directives); `labels.py` labels forgeable + `reap(owner=None)` cross-owner; `_combined_logs` dup.
- **v2** `llama_cpp/provider.py:298-304` `.raw` mutated with synthetic keys + non-deterministic tool-call id (replay drift); `cli.py` `session_id` path-join accepts `../` traversal; `llm/process.py` parent leaks log fd; several hardcoded tunables bypass `defaults.py` (`term_timeout`, pricing cache age, health poll).
- **websearch** Google PSE API key in URL query (log exposure); upstream error body echoed into ToolError.
- **gcs** 24h signed URL flows into model context; allowlist-denial ToolError discloses full bucket list.
- **ghidra** unbounded request-body read (JVM OOM); raw `e.getMessage()` in 500 body discloses local paths.
- **briefbot** `immutable=1` disables locking vs. the concurrent ingestor (torn reads); DB path in event payloads.
- **safety_gate** `catalog.py:69` `redirect-overwrite` regex false-positives on `->` (nuisance prompts → blind-approve training).

---

## What is already done right (security positives)

- **No `shell=True`, no `eval`/`exec`/`pickle`/`os.system`** anywhere in v2 core; `bash_exec` is the one intentional shell tool, gated by guard. `yaml.safe_load` for config; ruamel round-trip for mutation.
- **cos stdin path is correctly `shlex.join`-quoted** and the MCP `command:str` is `shlex.split` to exec-form (no shell) — that path is **not** injectable. Loopback-only port publishing and `host`/`container:*` network rejection are real and tested.
- **Plugin coupling is clean** — all five external plugins import only `arc.plugin_api`; none reach into arc internals.
- **arc-plugin-briefbot** is a model read-only integration: `mode=ro`, fully parameterized SQL, config-only DB path.
- **arc-plugin-gcs** enforces a single central bucket-allowlist chokepoint (`parse_uri→check_allowed`) with no per-tool gap; `gcs_delete` always gated, single-object only; signed URLs kept out of event payloads; SA key never logged.
- **arc-plugin-ghidra** binds loopback-only, exposes 11 fixed typed routes (no eval/script endpoint), mutations transactional/undoable, host/port from config not tool input.
- **arc-plugin-angr** has no eval/exec/subprocess; `auto_load_libs=False`; a three-brake bounded step loop (no unbounded `explore()`).
- **MCP async↔sync bridge** and the **two-layer sub-agent recursion prohibition** are carefully, correctly implemented; the guard's delegate-rule fail-open is deliberate and right.
- **Secrets** are never persisted (config stores only `api_key_env` *names*); `.env` is read with `setdefault` so real env wins; auth headers are redacted in emitted events.

---

## Recommended remediation order (highest ROI first)

1. **cos: default cap-drop + `no-new-privileges` + `pids_limit` + default cpu/mem caps** (H4) and **validate/deny bind-mount sources** (C1). These two convert cos from "convenience plane for a trusted user" toward "actually contains a workload." Also fix `_find` managed-scope (M5) and exclude `image_build` tags from `gc` (M4).
2. **cos + ghidra: add a bearer token + Origin/Host validation** (C2, H5). Small change, closes the local-service CSRF/rebind class.
3. **websearch: one shared SSRF validator** in `http.py` — scheme allowlist + resolved-IP denylist + per-redirect re-validation + streamed size cap. Closes C3/C4/C5/H8/H9 at one seam.
4. **Sub-agent policy** (H1/H2): give child sessions at least `guard`+`safety_gate`, and drop `bash_exec` from `container_expert` (or replace with a temp-dir-scoped write + curl tool). This restores the enforcement model.
5. **gcs: confine `gcs_download` to a `download_dir`, treat every download as a host-write op** (C6, H10).
6. **Fail-closed policy plugins** (H3) and **tool-call-cap synthetic results** (M2) in the runtime.
