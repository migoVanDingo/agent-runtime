"""Token-cost lookup, backed by LiteLLM's community-maintained pricing JSON.

Source:
  https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json

LiteLLM publishes a JSON mapping `<model_name>` → `{input_cost_per_token, ...}`
and updates it routinely as new models ship. We fetch it on first use, cache
for a week, and look up by trying multiple key variants (provider-prefixed,
date-suffixed, etc.) to handle the naming variance.

If the fetch fails AND there's no cache, lookups return None and the toolbar
hides the cost column. We never crash and we never hardcode prices that go
stale.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


_LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
_CACHE_MAX_AGE_SECONDS = 7 * 24 * 3600  # 1 week
_FETCH_TIMEOUT_SECONDS = 10

# Local-inference providers: never billed per-token, regardless of model.
_LOCAL_FREE_PROVIDERS = frozenset({"ollama", "llama_cpp"})
_LOCAL_FREE_RATES: dict[str, Any] = {
    "input_cost_per_token": 0.0,
    "output_cost_per_token": 0.0,
}


def _per_million(inp: float, out: float) -> dict[str, float]:
    return {"input_cost_per_token": inp / 1e6, "output_cost_per_token": out / 1e6}


# Curated fallback rates ($ per 1M tokens) for arc's known models — consulted
# when the LiteLLM fetch fails (offline / macOS-SSL) OR the model is too new for
# LiteLLM's DB (e.g. gemini-3.5-flash). Keep in sync with provider price pages;
# these are list prices (no cache/batch discounts). Verified 2026-07.
_STATIC_RATES: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-opus-4-8": _per_million(5, 25),
    "claude-opus-4-7": _per_million(5, 25),
    "claude-opus-4-6": _per_million(5, 25),
    "claude-sonnet-5": _per_million(3, 15),
    "claude-sonnet-4-6": _per_million(3, 15),
    "claude-haiku-4-5": _per_million(1, 5),
    "claude-fable-5": _per_million(10, 50),
    # Google Gemini
    "gemini-3.5-flash": _per_million(1.5, 9),
    "gemini-2.5-pro": _per_million(1.25, 10),
    "gemini-2.5-flash": _per_million(0.30, 2.50),
}


class PricingTable:
    """A lazy, cached, gracefully-failing pricing lookup.

    Lifecycle:
        table = PricingTable(cache_path=Path("~/.arc/pricing_cache.json"))
        info = table.lookup_for(provider="anthropic", model="claude-haiku-4-5")
        if info:
            cost = info["input_cost_per_token"] * input_tokens + ...
    """

    def __init__(self, cache_path: Path) -> None:
        self._cache_path = cache_path
        self._data: dict[str, dict[str, Any]] | None = None
        self._load_attempted = False

    def lookup_for(self, *, provider: str, model: str) -> dict[str, Any] | None:
        """Return pricing dict for the (provider, model) or None if unknown.

        Tries several key variants since LiteLLM's naming isn't strictly
        consistent with what each SDK uses.  Local providers (ollama,
        llama_cpp) fall back to a built-in $0 entry so the TUI can still
        show "$0.00" instead of an empty cost slot.
        """
        # Local providers are always free, even if LiteLLM doesn't list them
        # and even if the upstream fetch failed (no network on the inference
        # host is a common case).
        if provider in _LOCAL_FREE_PROVIDERS:
            return _LOCAL_FREE_RATES

        data = self._get_data()
        if data:
            # Tried in order; first hit wins
            candidates = [
                f"{provider}/{model}",
                model,
                # Some Gemini models in LiteLLM have a leading "gemini/" prefix
                f"gemini/{model}" if provider == "gemini" else None,
                # Anthropic sometimes uses "anthropic." prefix in API or "claude-X-Y-latest"
                f"anthropic.{model}" if provider == "anthropic" else None,
            ]
            for c in candidates:
                if c and c in data:
                    entry = data[c]
                    if isinstance(entry, dict) and "input_cost_per_token" in entry:
                        return entry

        # Static fallback — offline / fetch-failed, or a model newer than
        # LiteLLM's DB. Keyed by bare model name.
        return _STATIC_RATES.get(model) or _STATIC_RATES.get(f"{provider}/{model}")

    def estimate_cost_usd(self, *, provider: str, model: str,
                         input_tokens: int, output_tokens: int) -> float | None:
        """Convenience: lookup pricing + compute dollar cost. None on miss."""
        info = self.lookup_for(provider=provider, model=model)
        if not info:
            return None
        in_rate = float(info.get("input_cost_per_token", 0))
        out_rate = float(info.get("output_cost_per_token", 0))
        return input_tokens * in_rate + output_tokens * out_rate

    # ── Cache management ───────────────────────────────────────────────

    def _get_data(self) -> dict[str, dict[str, Any]] | None:
        if self._data is not None:
            return self._data
        if self._load_attempted:
            return self._data  # already tried once this run
        self._load_attempted = True

        # Try cache first
        cached = self._read_cache()
        if cached is not None and not self._cache_is_stale(cached):
            self._data = cached.get("data")
            return self._data

        # Refresh from upstream
        fresh = self._fetch_upstream()
        if fresh is not None:
            self._write_cache(fresh)
            self._data = fresh
            return self._data

        # Fetch failed — fall back to stale cache if any
        if cached is not None:
            self._data = cached.get("data")
            return self._data

        # No cache, no fresh data — caller gets None forever this run
        return None

    def _read_cache(self) -> dict | None:
        if not self._cache_path.is_file():
            return None
        try:
            return json.loads(self._cache_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write_cache(self, data: dict) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps({
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "fetched_at_ts": int(time.time()),
                    "data": data,
                }, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass  # cache failure is non-fatal

    @staticmethod
    def _cache_is_stale(cached: dict) -> bool:
        ts = cached.get("fetched_at_ts", 0)
        return (time.time() - ts) > _CACHE_MAX_AGE_SECONDS

    @staticmethod
    def _fetch_upstream() -> dict | None:
        """Fetch the LiteLLM JSON. Returns None on any failure."""
        try:
            req = Request(_LITELLM_URL, headers={"User-Agent": "arc-cli/1"})
            with urlopen(req, timeout=_FETCH_TIMEOUT_SECONDS) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
            return None
        except (URLError, OSError, json.JSONDecodeError):
            return None


def format_cost(cost_usd: float | None) -> str:
    """Human-readable cost. None → empty string (toolbar shows nothing)."""
    if cost_usd is None:
        return ""
    if cost_usd < 0.01:
        # Show 4 decimal places for very small amounts so users see it growing
        return f"${cost_usd:.4f}"
    if cost_usd < 1.0:
        return f"${cost_usd:.3f}"
    return f"${cost_usd:.2f}"
