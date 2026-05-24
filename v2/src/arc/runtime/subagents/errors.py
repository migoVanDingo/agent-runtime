"""Sub-agent error types — re-exported via arc.subagent_api.

All inherit from SubAgentError so callers can catch the family.
"""
from __future__ import annotations


class SubAgentError(Exception):
    """Base class for every sub-agent failure.

    Surfaces to the parent agent as a ToolError carrying the message.
    """


class SubAgentTimeoutError(SubAgentError):
    """The child exceeded its `timeout_s`. Child was cancelled cleanly."""


class SubAgentRecursionError(SubAgentError):
    """A sub-agent attempted to dispatch another sub-agent.

    Hard-prohibited by design. Raised by the tripwire layer; should never
    fire under normal use because the registry filter prevents
    SubAgentTool from being registered inside a child session.
    """
