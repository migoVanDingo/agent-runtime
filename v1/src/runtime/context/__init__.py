"""runtime.context package — pluggable context-management strategies.

Built-in strategies:
    - ``afm`` (default) — AFM-inspired non-destructive packing with similarity +
      recency + importance scoring and FULL/COMPRESSED/PLACEHOLDER fidelity tiers.
    - ``truncate`` — drop oldest messages until under budget.
    - ``sliding`` — keep last N messages verbatim; older messages collapse
      into a single LLM-generated summary.
    - ``rag`` — pack only the messages whose embeddings are semantically
      relevant to the current query.

Choose via ``runtime.context.strategy`` in config.yml. Each strategy receives
its parameters via ``runtime.context.params.<strategy>``.
"""
from runtime.context.factory import build_strategy, known_strategies, register_strategy
from runtime.context.manager import ContextManager  # noqa: F401  (back-compat)
from runtime.context.strategy import ContextStrategy

__all__ = [
    "ContextManager",
    "ContextStrategy",
    "build_strategy",
    "known_strategies",
    "register_strategy",
]
