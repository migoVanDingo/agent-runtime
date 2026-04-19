"""Generic multi-agent council deliberation primitive.

Two modes:
  independent — all councillors generate in parallel (blind), then algorithmic synthesis
  debate      — multiple rounds; each councillor sees prior round outputs and can revise

Usage:
    council = Council(adapter=MyAdapter(...), config=app_config.runtime.council)
    result = council.deliberate(input_obj, context="my_component", query="...")
    final_decision = result.final
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from messenger import Messenger
from providers.base import TextBlock
from providers.factory import get_provider
from logger import get_logger, council_tag, council_header_tag, synth_tag

logger = get_logger(__name__)

T = TypeVar("T")


# ── Data types ──────────────────────────────────────────────────────────────

@dataclass
class Councillor:
    provider: str
    label: str
    model: str | None = None


@dataclass
class CouncillorDecision:
    label: str
    provider: str
    model: str | None
    raw_response: str
    parsed: Any          # typed T by the adapter
    round_number: int    # 1-indexed


@dataclass
class CouncilRound:
    round_number: int
    decisions: list[CouncillorDecision]
    converged: bool = False


@dataclass
class CouncilRunMetrics:
    run_id: str
    context: str
    query: str
    mode: str
    rounds_completed: int
    councillor_labels: list[str]
    per_councillor_decisions: dict   # label → summarized decision
    agreement_map: dict              # per challenged item → challengers/approvers/ratio
    synthesis_trace: list[str]       # human-readable steps of synthesis logic
    final_verdict: str
    user_outcome: dict | None = None # filled in later if user is polled


@dataclass
class CouncilResult(Generic[T]):
    rounds: list[CouncilRound]
    final: T
    agreement_map: dict
    synthesis_trace: list[str]
    metrics: CouncilRunMetrics


# ── Deliberation adapter interface ──────────────────────────────────────────

class DeliberationAdapter(ABC, Generic[T]):
    """Adapts a specific deliberation task (e.g. plan criticism) to the Council interface.

    Implementors provide:
      - how to build the councillor prompt
      - how to parse the councillor response
      - how to synthesize N decisions into one final decision
      - how to detect convergence (for debate early-exit)
      - how to summarize a decision for metrics
    """

    @abstractmethod
    def system_prompt(self) -> str:
        """System prompt sent to every councillor."""
        ...

    @abstractmethod
    def build_prompt(self, council_input: Any, prior_rounds: list[CouncilRound] | None = None) -> str:
        """Build the user-turn prompt for a councillor.

        prior_rounds is None in independent mode.
        In debate mode it contains all prior rounds' decisions from all councillors.
        """
        ...

    @abstractmethod
    def parse_response(self, raw: str) -> T:
        """Parse a councillor's raw text response into a typed decision."""
        ...

    @abstractmethod
    def synthesize(
        self,
        decisions: list[CouncillorDecision],
        consensus_threshold: float,
    ) -> tuple[T, dict, list[str]]:
        """Synthesize N councillor decisions into one final decision.

        Returns:
            (final_decision, agreement_map, synthesis_trace)
        """
        ...

    @abstractmethod
    def decisions_converged(self, decisions: list[T]) -> bool:
        """Return True if all decisions are equivalent — used for debate early-exit."""
        ...

    @abstractmethod
    def summarize_decision(self, decision: T) -> dict:
        """Return a JSON-serializable summary of a decision for metrics."""
        ...

    def format_prior_rounds(self, prior_rounds: list[CouncilRound]) -> str:
        """Default debate-mode prompt suffix — shows all prior round responses."""
        lines = ["\n\n--- Prior Round Responses ---"]
        for rnd in prior_rounds:
            for d in rnd.decisions:
                lines.append(f"\n[{d.label}, round {d.round_number}]:\n{d.raw_response}")
        lines.append(
            "\n\nYou have seen the above responses from the other councillors. "
            "Reconsider your position. If their arguments are sound, you may revise. "
            "If you still disagree, explain specifically why. "
            "Return the same JSON format as before."
        )
        return "".join(lines)


# ── Council ──────────────────────────────────────────────────────────────────

class Council(Generic[T]):
    """Multi-agent deliberation engine.

    Instantiate with an adapter and config, then call deliberate().
    """

    def __init__(self, adapter: DeliberationAdapter[T], config):
        """
        Args:
            adapter: domain-specific adapter
            config:  CouncilConfig (from app_config.runtime.council)
        """
        self.adapter = adapter
        self.config = config

    def deliberate(self, council_input: Any, context: str = "", query: str = "") -> CouncilResult[T]:
        """Run council deliberation and return a CouncilResult."""
        councillors = [
            Councillor(provider=c.provider, label=c.label, model=c.model)
            for c in self.config.councillors
        ]
        labels = [c.label for c in councillors]

        if self.config.mode == "debate":
            return self._deliberate_debate(council_input, councillors, context, query)

        # Default: independent mode
        logger.info(f"  {council_header_tag()} independent — councillors: {labels}")
        round1 = self._run_round(council_input, councillors, round_number=1, prior_rounds=None)
        return self._build_result([round1], councillors, context, query)

    # ── Independent mode ─────────────────────────────────────────────────────

    def _deliberate_debate(self, council_input, councillors, context, query):
        max_rounds = self.config.debate.max_rounds
        early_exit = self.config.debate.early_exit_on_consensus
        labels = [c.label for c in councillors]
        logger.info(f"  {council_header_tag()} debate — councillors: {labels}  max_rounds: {max_rounds}")

        rounds = []
        for rnd_num in range(1, max_rounds + 1):
            prior = rounds if rnd_num > 1 else None
            rnd = self._run_round(council_input, councillors, round_number=rnd_num, prior_rounds=prior)
            rounds.append(rnd)
            if early_exit and rnd.converged:
                logger.info(f"  {council_header_tag()} converged at round {rnd_num} — early exit")
                break

        return self._build_result(rounds, councillors, context, query)

    # ── Round execution ───────────────────────────────────────────────────────

    def _run_round(
        self,
        council_input: Any,
        councillors: list[Councillor],
        round_number: int,
        prior_rounds: list[CouncilRound] | None,
    ) -> CouncilRound:
        logger.info(f"  {council_header_tag()} ── Round {round_number} {'─' * 40}")

        # Build the shared prompt once — all councillors in a round see the same input
        prompt = self.adapter.build_prompt(council_input, prior_rounds)
        if prior_rounds:
            prompt += self.adapter.format_prior_rounds(prior_rounds)

        n_workers = self.config.max_workers or len(councillors)
        decisions: list[CouncillorDecision | None] = [None] * len(councillors)

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = {
                pool.submit(self._query_one, councillor, prompt, round_number): i
                for i, councillor in enumerate(councillors)
            }
            for future in as_completed(futures):
                idx = futures[future]
                councillor = councillors[idx]
                try:
                    decisions[idx] = future.result()
                except Exception as e:
                    logger.warning(f"  {council_tag(councillor.label)} query failed: {e} — excluding from synthesis")
                    # Degrade gracefully: substitute an approved decision so synthesis continues
                    decisions[idx] = CouncillorDecision(
                        label=councillor.label,
                        provider=councillor.provider,
                        model=councillor.model,
                        raw_response="",
                        parsed=self.adapter.parse_response(""),
                        round_number=round_number,
                    )

        # Filter out any Nones (shouldn't happen, but be safe)
        clean_decisions = [d for d in decisions if d is not None]
        converged = self.adapter.decisions_converged([d.parsed for d in clean_decisions])
        if converged:
            logger.info(f"  {council_header_tag()} round {round_number}: all councillors converged")

        return CouncilRound(round_number=round_number, decisions=clean_decisions, converged=converged)

    def _query_one(self, councillor: Councillor, prompt: str, round_number: int) -> CouncillorDecision:
        """Query a single councillor. Runs in a thread pool worker."""
        logger.info(f"  {council_tag(councillor.label)} querying {councillor.provider}...")

        provider = get_provider(councillor.provider, councillor.model)
        messenger = Messenger()
        messenger.add_user_message(prompt)

        response = provider.chat(
            messages=messenger.get_messages(),
            tools=[],
            system=self.adapter.system_prompt(),
        )
        raw = next((b.text for b in response.content if isinstance(b, TextBlock)), "")

        logger.info(f"  {council_tag(councillor.label)} response:\n{raw}")

        parsed = self.adapter.parse_response(raw)
        summary = self.adapter.summarize_decision(parsed)
        logger.info(f"  {council_tag(councillor.label)} decision: {summary}")

        return CouncillorDecision(
            label=councillor.label,
            provider=councillor.provider,
            model=councillor.model,
            raw_response=raw,
            parsed=parsed,
            round_number=round_number,
        )

    # ── Result assembly ───────────────────────────────────────────────────────

    def _build_result(
        self,
        rounds: list[CouncilRound],
        councillors: list[Councillor],
        context: str,
        query: str,
    ) -> CouncilResult[T]:
        # Synthesize using decisions from the final round
        final_round = rounds[-1]
        final, agreement_map, trace = self.adapter.synthesize(
            final_round.decisions,
            self.config.consensus_threshold,
        )

        logger.info(f"  {synth_tag()} synthesis:")
        for line in trace:
            logger.info(f"    {synth_tag()} {line}")
        logger.info(f"  {synth_tag()} final: {self.adapter.summarize_decision(final)}")

        run_id = uuid.uuid4().hex[:8]
        metrics = CouncilRunMetrics(
            run_id=run_id,
            context=context,
            query=query,
            mode=self.config.mode,
            rounds_completed=len(rounds),
            councillor_labels=[c.label for c in councillors],
            per_councillor_decisions={
                d.label: self.adapter.summarize_decision(d.parsed)
                for d in final_round.decisions
            },
            agreement_map=agreement_map,
            synthesis_trace=trace,
            final_verdict=self.adapter.summarize_decision(final).get("verdict", str(final)),
        )

        # Persist metrics record
        from runtime.council_metrics import get_metrics_writer
        writer = get_metrics_writer()
        if writer:
            writer.record_run(metrics)

        return CouncilResult(
            rounds=rounds,
            final=final,
            agreement_map=agreement_map,
            synthesis_trace=trace,
            metrics=metrics,
        )
