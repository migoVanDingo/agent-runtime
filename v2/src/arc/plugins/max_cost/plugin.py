"""Cost-cap enforcement plugin used by cross-provider replay (0019).

Hooks `after_llm_call`, accumulates input+output cost using the shared
PricingTable, and raises `MaxCostExceeded` once the running total exceeds
the configured cap.  The runtime catches the exception, emits
`session.aborted`, and ends the session cleanly.

Local providers (ollama, llama_cpp) always cost $0 via the table's
local-free path, so this plugin is effectively a cloud-provider safety
net.  An unknown-rate model is also skipped (no enforcement), with a
warning event so the user knows the cap isn't being applied.
"""
from __future__ import annotations

import logging

from arc.runtime.events import EventType, RuntimeEvent, Severity
from arc.tui.pricing import PricingTable

log = logging.getLogger("arc.plugins.max_cost")


class MaxCostExceeded(RuntimeError):
    """Raised after_llm_call when the running total exceeds the cap.

    The runtime catches this, marks the session aborted (reason=cost_cap),
    and ends the loop with a recorded message.
    """


class MaxCostPlugin:
    """v1 — after_llm_call enforcer.

    Construct with the cap (USD) and a PricingTable.  Bind the bus via
    `bind_bus()` so the plugin can emit `session.aborted` before the
    runtime catches the raise.
    """

    name = "max_cost"

    def __init__(self, *, max_cost_usd: float, pricing_table: PricingTable) -> None:
        self._cap = float(max_cost_usd)
        self._table = pricing_table
        self._running_usd = 0.0
        self._bus = None
        self._warned_unknown_rate: set[tuple[str, str]] = set()

    def bind_bus(self, bus) -> None:
        self._bus = bus

    @property
    def running_usd(self) -> float:
        return self._running_usd

    def after_llm_call(self, ctx, req, resp):
        """Tally cost; raise MaxCostExceeded when the cap is breached."""
        provider = ctx.session.provider_name
        model = ctx.session.provider_model
        rates = self._table.lookup_for(provider=provider, model=model)
        if rates is None:
            key = (provider, model)
            if key not in self._warned_unknown_rate:
                self._warned_unknown_rate.add(key)
                log.warning(
                    "max_cost: no pricing data for %s/%s — cap is NOT enforced for this run",
                    provider, model,
                )
            return None

        delta = (resp.input_tokens * float(rates.get("input_cost_per_token", 0.0))
                 + resp.output_tokens * float(rates.get("output_cost_per_token", 0.0)))
        self._running_usd += delta

        if self._running_usd > self._cap:
            if self._bus is not None:
                self._bus.emit(RuntimeEvent(
                    type=EventType.SESSION_ABORTED,
                    stage="MaxCostPlugin",
                    severity=Severity.WARN,
                    payload={
                        "reason": "cost_cap",
                        "running_usd": round(self._running_usd, 6),
                        "cap_usd": self._cap,
                        "provider": provider,
                        "model": model,
                    },
                ))
            raise MaxCostExceeded(
                f"replay aborted: cost ${self._running_usd:.4f} exceeded cap ${self._cap:.2f}"
            )

        return None
