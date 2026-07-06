"""sessions dir → Forest.

Lineage is read meta-first (cheap, and eager-stamping since 0026 makes it
reliable), with a fallback to the `session.branched` event for sessions whose
meta lacks it — a session hard-killed before its lineage was durably stamped
still has the event, which is the authoritative record.

Cost is intentionally NOT computed here: it needs a PricingTable (network/
cache) and isn't deterministic, so it's a render-time enrichment. The scan is
pure over on-disk bytes → golden-testable.
"""
from __future__ import annotations

import json
from pathlib import Path

from arc.runtime.events import EventType
from arc.timeline.model import Edge, Forest, SessionNode, TurnNode
from arc.timeline.summarize import DEFAULT_SUMMARY_MAX_CHARS, load_or_build_node_cache

# meta lineage field → (SessionNode attr, edge kind, is-fork-point)
_LINEAGE = (
    ("replay_of", "replay_of", "replay"),
    ("rerun_of", "rerun_of", "rerun"),
    ("resumed_from", "resumed_from", None),  # branch vs resume vs retry: refined below
)


def scan_forest(sessions_dir: Path, *, summary_max_chars: int = DEFAULT_SUMMARY_MAX_CHARS) -> Forest:
    """Build the forest from every SES* dir under `sessions_dir`.

    Order: by created_at (index.jsonl order is roughly chronological but not
    authoritative — meta's started_at is). Missing dirs / unreadable metas are
    skipped, never fatal.
    """
    if not sessions_dir.is_dir():
        return Forest()

    nodes: dict[str, SessionNode] = {}
    for child in sessions_dir.iterdir():
        if not child.is_dir() or not child.name.startswith("SES"):
            continue
        node = _build_node(child, summary_max_chars=summary_max_chars)
        if node is not None:
            nodes[node.sid] = node

    edges = _derive_edges(nodes)
    _mark_orphans(nodes, edges)

    ordered = sorted(nodes.values(), key=lambda n: (n.created_at or "", n.sid))
    # A session is a root unless it hangs off a parent that is actually on
    # disk. Orphans (parent wiped/missing) render as roots with a badge.
    attached = {e.child_sid for e in edges if e.parent_sid in nodes}
    roots = [n.sid for n in ordered if n.sid not in attached]

    return Forest(nodes=ordered, edges=edges, roots=roots)


def _build_node(session_dir: Path, *, summary_max_chars: int) -> SessionNode | None:
    meta = _read_json(session_dir / "meta.json") or {}
    sid = meta.get("session_id") or session_dir.name

    cache = load_or_build_node_cache(session_dir, summary_max_chars=summary_max_chars)

    node = SessionNode(
        sid=sid,
        created_at=meta.get("started_at"),
        ended_at=meta.get("ended_at"),
        provider=meta.get("provider") or cache.get("provider", "?"),
        model=meta.get("model") or cache.get("model", "?"),
        turn_count=int(cache.get("turn_count", 0)),
        input_tokens=int(cache.get("input_tokens", 0)),
        output_tokens=int(cache.get("output_tokens", 0)),
        status=cache.get("status", "unknown"),
        turns=[TurnNode.from_dict(t) for t in cache.get("turns", [])],
    )

    _apply_lineage(node, meta, session_dir)
    return node


def _apply_lineage(node: SessionNode, meta: dict, session_dir: Path) -> None:
    """Fill lineage fields from meta, falling back to the session.branched
    event when meta is missing a branch stamp (hard-killed before stamping)."""
    node.replay_of = meta.get("replay_of")
    node.replay_mode = meta.get("replay_mode")
    node.rerun_of = meta.get("rerun_of")
    node.resumed_from = meta.get("resumed_from")
    node.branched_at_turn = meta.get("branched_at_turn")
    node.retry_of_turn = meta.get("retry_of_turn")
    node.provider_override = meta.get("provider_override")

    # Fallback: meta lacks branch lineage but the event has it.
    if node.resumed_from is None:
        ev = _find_branch_event(session_dir)
        if ev is not None:
            node.resumed_from = ev.get("source_session_id")
            node.branched_at_turn = ev.get("branched_at_turn")
            if ev.get("retry_of_turn") is not None:
                node.retry_of_turn = ev.get("retry_of_turn")


def _derive_edges(nodes: dict[str, SessionNode]) -> list[Edge]:
    """One edge per lineage relationship. A session has at most one parent
    stamp in practice; if several are set, replay/rerun win over resume."""
    edges: list[Edge] = []
    for node in nodes.values():
        if node.replay_of:
            edges.append(Edge(node.replay_of, node.sid, "replay", None))
        elif node.rerun_of:
            edges.append(Edge(node.rerun_of, node.sid, "rerun", None))
        elif node.resumed_from:
            if node.retry_of_turn is not None:
                kind, pturn = "retry", node.branched_at_turn
            elif node.branched_at_turn is not None:
                kind, pturn = "branch", node.branched_at_turn
            else:
                kind, pturn = "resume", None  # full-history resume, attach at end
            edges.append(Edge(node.resumed_from, node.sid, kind, pturn))
    return edges


def _mark_orphans(nodes: dict[str, SessionNode], edges: list[Edge]) -> None:
    for e in edges:
        if e.parent_sid not in nodes:
            nodes[e.child_sid].parent_missing = True


def _find_branch_event(session_dir: Path) -> dict | None:
    events_path = session_dir / "events.jsonl"
    if not events_path.is_file():
        return None
    for line in events_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or EventType.SESSION_BRANCHED not in line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == EventType.SESSION_BRANCHED:
            return e.get("payload", {})
    return None


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
