# 0041 — Web Toolset

## Overview

Added the `web` toolset — the first realm that reaches outside the local
filesystem. Three tools, a two-layer security inspector, injection gate
integration in both execution stages, guard escalations for all outbound
network activity, and a directory restructure of `tools/implementations/`.

---

## Directory Restructure

`tools/implementations/` was a flat directory of 29 files. Split into
subdirectories by realm so each new toolset has a clean home:

```
tools/implementations/
  file_io/     — 13 tools (read, write, list, walk, copy, move, delete, mkdir, etc.)
  shell/       — 2 tools  (bash_exec, search_files)
  analysis/    — 10 tools (strings, objdump, hexdump, nm, readelf, ltrace, strace, etc.)
  crypto/      — 4 tools  (hash_file, base64, xor_decode)
  web/         — 3 tools  (http_request, read_url, extract_html)
```

Each subdirectory has its own `__init__.py`. All imports in `toolsets.py`
updated to the new paths. Verified clean import with venv.

---

## Guard: Network Command Escalation

**Problem:** `curl` and `wget` were only blocked in the `bash_exec` guard
when piped to a shell (`curl ... | sh`). All other uses — API calls, file
downloads, exfiltration via `scp`/`ssh` — passed through silently.

**Fix:** Added `_NETWORK_COMMANDS` escalation pattern to `guard.py`:

```
curl, wget, scp, ssh, sftp, rsync, ftp, nc/ncat/netcat, socat
```

All of these → ESCALATE in `_check_shell_command`. The pipe-to-shell variants
(`curl | sh`, `wget | sh`) remain in `_DANGEROUS_COMMANDS` as BLOCK since
they are unambiguously malicious.

This closes the gap where an agent could silently exfiltrate files via
`scp file user@host:` or establish a reverse shell via `nc`.

---

## Web Tools

### `http_request`

Full HTTP client. Inputs: `method`, `url`, `headers`, `params`, `body`,
`timeout`. Auto-detects JSON body and sets `Content-Type`. Auto-pretty-prints
JSON responses. Truncates response bodies at 50k chars.

Guard: always ESCALATE with `"outbound HTTP {METHOD} → {url}"` as reason.
Approval cache key: `http_request:{METHOD}:{url}` — same method+URL doesn't
re-prompt within a session.

Dependency: `httpx` (already present via `anthropic`).

### `read_url`

Fetch a web page and extract clean readable text. The quarantine architecture:

1. Fetch via `httpx`
2. Extract clean text via `trafilatura` (strips nav, ads, scripts). Falls back
   to a minimal stdlib `html.parser` stripper if trafilatura unavailable.
3. Write full content to `/tmp/agent_fetch_{url_hash}.txt` — content never
   enters conversation context from this tool
4. Run two-layer injection scan (see WebInspector below)
5a. Safe: return file path + title + char count + 300-char preview.
    Agent reads via `read_file` or `read_file_lines`.
5b. Unsafe: return `INJECTION_WARNING_PREFIX` sentinel + warning details +
    quarantine path. Execution stage halts and gates on user approval.

Long content (papers, blogs) is handled automatically: the full text is on
disk, the agent reads it in chunks via `read_file_lines` like any other file.
No paging complexity needed.

Guard: ESCALATE for all fetch operations.

Dependency: `trafilatura`, `beautifulsoup4`.

### `extract_html`

CSS selector extraction. Accepts a URL or raw HTML string as `source`.
Returns text content of matched elements, or a specified attribute value
(e.g. `href` from `a` tags). Caps at 200 results. Used for structured
scraping where `read_url`'s prose extraction is too blunt.

Guard: ESCALATE when source is a URL.

---

## WebInspector

**File:** `src/runtime/web_inspector.py`

Isolated two-layer scanner. No conversation history shared with the main
agent — completely separate context.

### Layer 1 — Regex (instant)

`_INJECTION_PATTERNS` matches 12 families of known injection phrases:
- `ignore previous instructions`
- `your new task / goal / objective`
- `you are now a ...`
- `new system prompt`
- `forget everything / your training`
- `override your instructions / safety / restrictions`
- `jailbreak`, `DAN mode`
- `pretend you are / have no / there are no`
- `act as a different / evil / unrestricted ...`
- `from now on you will / must / should`
- `disregard your ...`

If Layer 1 triggers → immediately return `InspectionResult(safe=False)`,
skip Layer 2.

### Layer 2 — Haiku inspector (isolated LLM call)

Only runs if Layer 1 passes. Uses `get_runtime_provider()` (Haiku).
System prompt is a hardened classifier instruction that explicitly tells the
model to treat all content as untrusted data and never follow embedded
instructions. Returns JSON:

```json
{"safe": bool, "confidence": "high|medium|low", "reason": "...", "flagged_excerpts": [...]}
```

Input is truncated to a representative 8k-char sample (first 6k + middle 2k)
to keep latency and cost low. Labeled `"WebInspector"` in the token tracker.

If Layer 2 fails (network error, parse error) → defaults to safe with
`confidence: "low"` — inspector failure is non-fatal.

### InspectionResult

```python
@dataclass
class InspectionResult:
    safe: bool
    confidence: str       # "high" | "medium" | "low"
    reason: str
    flagged_excerpts: list[str]
    layer1_triggered: bool
    layer2_triggered: bool
```

---

## Injection Gate (Execution Stages)

Added to both `DirectExecutionStage` and `ExecutionStage`, triggered when a
tool result starts with `INJECTION_WARNING_PREFIX = "[INJECTION_WARNING]"`.

**Flow when triggered:**
1. Stop the tool loop immediately
2. Print a clearly formatted security warning to the console, including the
   flagged excerpts and quarantine file path
3. Prompt user: `Proceed with reading this content? [y/N]`
4. If **yes**: replace the sentinel prefix with `[SECURITY REVIEW PASSED BY USER]`
   and continue normally
5. If **no**: offer to delete the quarantine file from disk. Replace result
   with a cancellation message. Inject a stop message into the messenger and
   set `force_end = True` so the agent wraps up cleanly without further tool
   calls.

The content never enters the LLM's context if the user says no — the
quarantine file is the only place it exists, and that gets deleted on request.

---

## Files Created / Modified

| File | Change |
|------|--------|
| `src/runtime/guard.py` | Added `_NETWORK_COMMANDS` pattern; added ESCALATE for `http_request`, `read_url`, `extract_html`; added approval cache keys for web tools |
| `src/runtime/web_inspector.py` | New — two-layer injection scanner |
| `src/tools/implementations/web/http_request.py` | New |
| `src/tools/implementations/web/read_url.py` | New — quarantine + inspection architecture |
| `src/tools/implementations/web/extract_html.py` | New |
| `src/tools/implementations/web/__init__.py` | New |
| `src/tools/implementations/{file_io,shell,analysis,crypto}/__init__.py` | New — subdirectory packages |
| `src/tools/toolsets.py` | Updated imports to subdirectory paths; added `WEB` toolset and `ALL_TOOLSETS` entry |
| `src/runtime/stages/direct_execution.py` | Added injection gate |
| `src/runtime/stages/execution.py` | Added injection gate |
| `requirements.txt` | Added `trafilatura`, `beautifulsoup4` |
