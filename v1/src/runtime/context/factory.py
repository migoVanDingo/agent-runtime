"""Strategy registry and factory.

The factory is the only place that knows the mapping from config string
(``"afm"``, ``"truncate"``, ...) to concrete strategy class. Other modules
should call ``build_strategy()`` rather than instantiating strategies directly.

Plugins (see ``_plans/0088-plugin-system.md``) can register additional
strategies via ``register_strategy(name, cls)``. The plugin loader is
expected to call this during the discovery phase if it surfaces an
``arc.context_strategies`` entry-point group in the future.
"""
from __future__ import annotations

from typing import Type

from logger import get_logger
from runtime.context.strategy import ContextStrategy

logger = get_logger(__name__)


_REGISTRY: dict[str, Type] = {}


def register_strategy(name: str, cls: Type) -> None:
    """Add a strategy class to the registry. Idempotent on identical re-registration."""
    name = name.lower()
    existing = _REGISTRY.get(name)
    if existing is cls:
        return
    if existing is not None:
        logger.warning(
            f"context strategy {name!r} re-registered "
            f"({existing.__name__} → {cls.__name__})"
        )
    _REGISTRY[name] = cls


def known_strategies() -> list[str]:
    """Return the sorted list of currently-registered strategy names."""
    return sorted(_REGISTRY)


def _ensure_builtins_registered() -> None:
    """Lazy registration of built-in strategies to avoid import cycles."""
    if "afm" not in _REGISTRY:
        from runtime.context.manager import ContextManager
        register_strategy("afm", ContextManager)
        # "default" is an alias kept stable for users who don't want to learn jargon.
        register_strategy("default", ContextManager)
    if "truncate" not in _REGISTRY:
        from runtime.context.strategies.truncation import TruncationStrategy
        register_strategy("truncate", TruncationStrategy)
    if "sliding" not in _REGISTRY:
        from runtime.context.strategies.sliding import SlidingWindowStrategy
        register_strategy("sliding", SlidingWindowStrategy)
    if "rag" not in _REGISTRY:
        from runtime.context.strategies.rag_aug import RagAugmentedStrategy
        register_strategy("rag", RagAugmentedStrategy)


def build_strategy(name: str | None = None) -> ContextStrategy:
    """Construct the strategy named in ``config.runtime.context.strategy``.

    When ``name`` is supplied, it takes precedence over the config value
    (useful for tests). Raises ``ValueError`` for unknown strategy names.
    """
    _ensure_builtins_registered()

    from app_config import config
    cfg = config.runtime.context
    chosen = (name or cfg.strategy or "afm").lower()
    cls = _REGISTRY.get(chosen)
    if cls is None:
        raise ValueError(
            f"unknown context strategy: {chosen!r} (known: {known_strategies()})"
        )
    params = dict(cfg.params.get(chosen, {}) or {})
    instance = cls(params=params)
    logger.info(f"context strategy: {chosen} ({cls.__name__})")
    return instance
