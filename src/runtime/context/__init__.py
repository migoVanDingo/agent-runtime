"""runtime.context package — re-exports ContextManager for back-compat."""
from runtime.context.manager import ContextManager  # noqa: F401

__all__ = ["ContextManager"]
