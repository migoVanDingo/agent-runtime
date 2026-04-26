# 0046 — Search Toolset (Brave API)

## Scope

A `search` toolset giving the agent structured web, news, and image search via the
Brave Search API. Distinct from the `web` toolset: the web toolset fetches and
reads specific pages; the search toolset returns result lists (title, URL, snippet)
from which the agent can decide which pages to read further.

Requires `BRAVE_API_KEY` environment variable. All tools fail gracefully with a
clear error if the key is missing.

---

## Tools

### `web_search`

Full web search via Brave Web Search API.

**Inputs:**
- `query` (required) — search query string
- `count` — number of results (1–20, default 10)
- `country` — 2-letter country code for geo-localized results (e.g. `"US"`)
- `freshness` — recency filter: `pd` (past day), `pw` (past week), `pm` (past month), `py` (past year)
- `safe_search` — `moderate` (default), `strict`, or `off`

**Output:** Formatted list of results — title, URL, description, age. Annotates
the first result with `[#1]` etc. Caps output at `count` results.

**Guard:** ALLOW — read-only GET to Brave API. No content injection risk
(returns structured JSON, not raw HTML). API key scope is pre-authorized by the
user setting `BRAVE_API_KEY`.

---

### `news_search`

News article search via Brave News Search API.

**Inputs:**
- `query` (required)
- `count` — 1–20, default 10
- `freshness` — same options as web_search; default `pw` (past week) for news
- `country` — optional

**Output:** Formatted list — title, URL, source name, published age, description.

**Guard:** ALLOW.

---

### `image_search`

Image search via Brave Image Search API.

**Inputs:**
- `query` (required)
- `count` — 1–10, default 5

**Output:** Formatted list — title, source URL, image URL, dimensions when
available. Useful for finding image assets or confirming visual content.

**Guard:** ALLOW.

---

## Routing Rules

```python
SEARCH = Toolset(
    name="search",
    planning_note=(
        "Use web_search to find information on a topic without a specific URL. "
        "Use news_search for current events or recent articles. "
        "Use image_search when looking for images. "
        "After web_search, use read_url to fetch and read the full content of a result."
    ),
    rules=[
        any_keyword(
            "search", "find", "look up", "look for", "google", "bing",
            "brave", "search the web", "web search", "find articles",
            "current events", "news", "latest", "recent", "what is",
            "who is", "when did", "image search", "find images",
        ),
        lambda msg, _: bool(re.search(
            r"\bsearch\s+for\b|\bfind\s+(?:me\s+)?(?:information|articles|images|news)\b"
            r"|\bwhat(?:'s|'s| is)\s+(?:the\s+)?(?:latest|current|recent)\b",
            msg, re.IGNORECASE,
        )),
    ],
)
```

---

## ActionType

Adds `SEARCH = "search"` to the `ActionType` enum and to `PLAN_JSON_SCHEMA`.

---

## Environment

`BRAVE_API_KEY` must be set in `.env` or shell environment. The tools return a
descriptive error if it is not found rather than raising an exception:

```
Error: BRAVE_API_KEY is not set. Add it to .env or export it in your shell.
```

---

## Dependencies

| Dependency | Already present? |
|-----------|----------------|
| `httpx` | Yes |
| `os.environ` | Yes (stdlib) |

No new dependencies required.

---

## Files

| File | Change |
|------|--------|
| `src/tools/implementations/search/__init__.py` | New |
| `src/tools/implementations/search/web_search.py` | New |
| `src/tools/implementations/search/news_search.py` | New |
| `src/tools/implementations/search/image_search.py` | New |
| `src/tools/toolsets.py` | Add SEARCH toolset + imports |
| `src/planning/schema.py` | Add `ActionType.SEARCH` |
| `config.yml` | Add `search` to `toolset_descriptions` |
