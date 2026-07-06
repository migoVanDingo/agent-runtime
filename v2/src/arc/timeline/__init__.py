"""Visual session timeline (0027) — the forest of sessions, rendered.

Sessions and their lineage (branch/resume/retry/replay/rerun) form a forest;
this package projects it into a self-contained static HTML page that lives in
the sessions dir alongside the recordings. Everything rebuilds from events +
meta — the timeline is just another projection (principle 2).

Layers:
  model.py      Forest / SessionNode / TurnNode / Edge dataclasses
  summarize.py  one session's events.jsonl → a node-cache dict (per-turn
                summaries + totals), cached as <sid>/timeline.node.json
  scan.py       sessions dir → Forest (meta-first lineage, event fallback)
  render.py     Forest → timeline.html / session.html   (phase b/c)
"""
from arc.timeline.model import Edge, Forest, SessionNode, TurnNode
from arc.timeline.scan import scan_forest

__all__ = ["Edge", "Forest", "SessionNode", "TurnNode", "scan_forest"]
