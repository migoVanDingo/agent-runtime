"""Service layer — the contract between any frontend and the agent runtime.

Public surface:
  AgentService    — Protocol for any service implementation
  TurnHandle      — Protocol for a single in-flight turn
  AgentEvent      — Union type of all typed events
  event_from_dict — Reconstruct an AgentEvent from a dict (for transport)
  event_to_json   — Serialize an AgentEvent to JSON string
  event_from_json — Deserialize an AgentEvent from JSON string
"""
from service.events import (
    AgentEvent,
    SessionStarted, SessionEnded,
    TurnStarted, TurnCompleted, TurnFailed, TurnCancelled,
    StageStarted, StageProgress, StageCompleted,
    TokenChunk, MessageComplete,
    ToolCallStarted, ToolCallCompleted,
    event_from_dict, event_to_json, event_from_json,
)
from service.interface import AgentService, TurnHandle
from service.errors import TurnCancelledError, TurnFailedError
from service.inprocess import InProcessAgentService, NoopSpinner, TUIUserGate, TUIInputGate
from service.queue import BoundedDropQueue

__all__ = [
    "AgentService", "TurnHandle", "AgentEvent",
    "InProcessAgentService", "NoopSpinner", "TUIUserGate", "TUIInputGate", "BoundedDropQueue",
    "SessionStarted", "SessionEnded",
    "TurnStarted", "TurnCompleted", "TurnFailed", "TurnCancelled",
    "StageStarted", "StageProgress", "StageCompleted",
    "TokenChunk", "MessageComplete",
    "ToolCallStarted", "ToolCallCompleted",
    "event_from_dict", "event_to_json", "event_from_json",
    "TurnCancelledError", "TurnFailedError",
]
