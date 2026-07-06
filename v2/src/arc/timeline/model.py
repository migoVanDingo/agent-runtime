"""Dataclasses for the session forest.

Plain data — no I/O, no rendering. scan.py builds these; render.py consumes
them. Everything is JSON-serializable (to_dict) so the node cache and the
embedded page payload share one shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Edge kinds, in the order the design's meta-stamp table lists them.
EDGE_KINDS = ("branch", "resume", "retry", "replay", "rerun")


@dataclass
class TurnNode:
    """One turn's summary — the node-cache unit and a timeline node."""
    index: int
    user_summary: str = ""
    assistant_summary: str = ""
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "user_summary": self.user_summary,
            "assistant_summary": self.assistant_summary,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "duration_s": round(self.duration_s, 3),
        }

    @classmethod
    def from_dict(cls, d: dict) -> TurnNode:
        return cls(
            index=int(d["index"]),
            user_summary=d.get("user_summary", ""),
            assistant_summary=d.get("assistant_summary", ""),
            tool_calls=int(d.get("tool_calls", 0)),
            input_tokens=int(d.get("input_tokens", 0)),
            output_tokens=int(d.get("output_tokens", 0)),
            duration_s=float(d.get("duration_s", 0.0)),
        )


@dataclass
class SessionNode:
    """One session: identity, totals, status, lineage, and its turns."""
    sid: str
    created_at: str | None = None
    ended_at: str | None = None
    provider: str = "?"
    model: str = "?"
    turn_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    status: str = "unknown"  # completed | aborted | running | empty
    is_subagent: bool = False

    # Lineage (raw meta/event stamps; edges are derived from these in scan)
    resumed_from: str | None = None
    branched_at_turn: int | None = None
    replay_of: str | None = None
    replay_mode: str | None = None
    rerun_of: str | None = None
    retry_of_turn: int | None = None
    provider_override: dict | None = None

    parent_missing: bool = False  # parent sid referenced but not on disk
    turns: list[TurnNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sid": self.sid,
            "created_at": self.created_at,
            "ended_at": self.ended_at,
            "provider": self.provider,
            "model": self.model,
            "turn_count": self.turn_count,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "status": self.status,
            "is_subagent": self.is_subagent,
            "resumed_from": self.resumed_from,
            "branched_at_turn": self.branched_at_turn,
            "replay_of": self.replay_of,
            "replay_mode": self.replay_mode,
            "rerun_of": self.rerun_of,
            "retry_of_turn": self.retry_of_turn,
            "provider_override": self.provider_override,
            "parent_missing": self.parent_missing,
            "turns": [t.to_dict() for t in self.turns],
        }


@dataclass
class Edge:
    """A lineage link. parent_turn is the fork point (branch/retry) or None
    (resume attaches at end; replay/rerun attach at the lane head)."""
    parent_sid: str
    child_sid: str
    kind: str  # one of EDGE_KINDS
    parent_turn: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "parent_sid": self.parent_sid,
            "child_sid": self.child_sid,
            "kind": self.kind,
            "parent_turn": self.parent_turn,
        }


@dataclass
class Forest:
    """The whole scan: session nodes, lineage edges, and the root sids
    (sessions with no present parent) in display order."""
    nodes: list[SessionNode] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "roots": list(self.roots),
        }
