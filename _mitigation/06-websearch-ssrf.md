# 06 — websearch SSRF seam

**Mitigates:** `02-security-audit.md` C3, C4, C5, H8, H9 (the whole SSRF cluster).
Path "B" of the 2026-07-05 pass.

## The problem
`arc-plugin-websearch` fetched agent-controlled URLs with no SSRF protection:
`http_request` / `extract_html` did **no** host validation, `read_url`'s check
was an exact-string denylist (`localhost`/`127.0.0.1`/`0.0.0.0`/`::1`) that
missed the cloud-metadata IP, all RFC1918 ranges, octal/decimal IPs, and
public names resolving to loopback; redirects were followed and never
re-validated; and there was no response-size cap (gzip-bomb → OOM).

## The fix — one shared seam
All three tools now route every fetch through **`http.safe_request()`**
(`arc-plugin-websearch/src/arc_plugin_websearch/http.py`):

- **`validate_url()`** — scheme allowlist (`http`/`https`) + resolves the host
  and rejects it if ANY resolved address is loopback / private / link-local /
  reserved / multicast / unspecified. Catches `169.254.169.254`, RFC1918,
  octal/decimal IPs, IPv4-mapped IPv6, and names resolving to loopback (H8, C3, C4).
- **Manual redirect following** — the client has `follow_redirects=False`;
  `safe_request` follows up to `MAX_REDIRECTS` hops and **re-validates every
  hop's Location** before following, so a `302 → 169.254.169.254` can't smuggle
  the agent to an internal host (C5). 303 downgrades to GET and drops the body.
- **Streamed size cap** — the body is read via `iter_bytes()` (which decodes
  gzip → real size) and aborted past `DEFAULT_MAX_BYTES` (10 MiB), defeating
  decompression bombs (H9).

`read_url`'s old `_BLOCKED_HOSTS` set is gone; the tools translate
`http.BlockedURLError` → `ToolError`.

## Verification
- New `tests/test_ssrf.py` (11 tests): metadata/private/loopback/unspecified IPs
  blocked, public allowed, bad schemes rejected, a redirect to a private IP
  raised, the size cap enforced, a within-cap body returned intact.
- `tests/conftest.py` adds an autouse resolver stub so the existing suite stays
  hermetic (no real DNS); the block tests still block (127.0.0.1/localhost
  resolve to themselves), the mocked success tests still pass.
- Full websearch suite: **78 passed** (67 existing + 11 new).

## Residual
- Not full DNS-rebind pinning: `validate_url` resolves and checks all A/AAAA
  records, and re-validates each redirect hop, but does not pin the connection
  to the validated IP — a TOCTOU window between resolve-and-connect remains
  (much narrower than before). True pinning needs a custom httpx transport.
- Search **backends** (brave/google/searxng) still use `http.client()` directly,
  not `safe_request` — deliberate: those hit fixed, user-configured API
  endpoints, not agent-controlled URLs, so SSRF doesn't apply.
