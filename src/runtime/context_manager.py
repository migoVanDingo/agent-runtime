"""Compat shim — ContextManager has moved to runtime.context.manager.

This module is preserved so existing imports continue to work:
    from runtime.context_manager import ContextManager
    from runtime.context_manager import _message_text
"""
from runtime.context.manager import ContextManager  # noqa: F401
from runtime.context.scoring import message_text as _message_text  # noqa: F401
