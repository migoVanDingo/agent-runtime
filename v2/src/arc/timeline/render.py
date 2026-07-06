"""Forest → timeline.html, session detail → session.html (0027 phases b/c).

Self-contained static pages: CSS/JS inline, data embedded via
`<script type="application/json">` (never string-concatenated into JS — tool
outputs are hostile bytes). No CDN, no fetch — they must work from file://.
The SVG forest is drawn client-side by the embedded JS from the forest JSON.
"""
from __future__ import annotations

import html
import json
from typing import Any

from arc.timeline.model import Forest

_STYLE = """
:root {
  --bg:#0d1117; --panel:#141b24; --line:#233040; --line-2:#1a2430;
  --ink:#dbe4ee; --ink-dim:#8b98a8; --ink-faint:#5b6675;
  --accent:#57c7c7; --accent-warm:#e2b25a; --violet:#b08cd6;
  --ok:#7bc47b; --warn:#e2b25a; --bad:#e06c6c;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
}
:root[data-theme="light"], :root:not([data-theme="dark"]) {
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) {
    --bg:#f5f7fa; --panel:#ffffff; --line:#d4dce6; --line-2:#e3e9f0;
    --ink:#1b2430; --ink-dim:#5a6675; --ink-faint:#8a95a5;
    --accent:#1f8f8f; --accent-warm:#b8863a; --violet:#8258b0;
    --ok:#3f9a4f; --warn:#b8863a; --bad:#c04848;
  }
}
:root[data-theme="light"] {
  --bg:#f5f7fa; --panel:#ffffff; --line:#d4dce6; --line-2:#e3e9f0;
  --ink:#1b2430; --ink-dim:#5a6675; --ink-faint:#8a95a5;
  --accent:#1f8f8f; --accent-warm:#b8863a; --violet:#8258b0;
  --ok:#3f9a4f; --warn:#b8863a; --bad:#c04848;
}
* { box-sizing:border-box; }
html,body { margin:0; height:100%; background:var(--bg); color:var(--ink);
  font-family:var(--sans); font-size:14px; }
a { color:var(--accent); }
.app { display:flex; flex-direction:column; height:100vh; }
header { display:flex; align-items:baseline; gap:20px; padding:14px 20px;
  border-bottom:1px solid var(--line); flex-wrap:wrap; }
header h1 { margin:0; font-size:15px; font-weight:600; letter-spacing:.04em;
  text-transform:uppercase; color:var(--ink); }
header h1 .mark { color:var(--accent); }
.stats { display:flex; gap:16px; color:var(--ink-dim); font-family:var(--mono);
  font-size:12px; font-variant-numeric:tabular-nums; }
.stats b { color:var(--ink); font-weight:600; }
.filter { margin-left:auto; }
.filter input { background:var(--panel); border:1px solid var(--line);
  color:var(--ink); border-radius:6px; padding:6px 10px; font-family:var(--mono);
  font-size:12px; width:200px; }
.filter input:focus { outline:2px solid var(--accent); outline-offset:-1px; }
.body { flex:1; display:flex; min-height:0; }
.canvas { flex:1; overflow:auto; position:relative; }
svg { display:block; }
.node rect { fill:var(--panel); stroke:var(--line); stroke-width:1.5;
  cursor:pointer; transition:stroke .12s; }
.node:hover rect { stroke:var(--accent); }
.node.sel rect { stroke:var(--accent); stroke-width:2.5; }
.node.aborted rect { stroke:var(--bad); }
.node.running rect { stroke-dasharray:3 3; }
.node text { fill:var(--ink-dim); font-family:var(--mono); font-size:10px;
  pointer-events:none; }
.lane-label { fill:var(--ink-faint); font-family:var(--mono); font-size:11px; }
.lane-label.orphan { fill:var(--warn); }
.edge { fill:none; stroke-width:1.6; }
.edge.branch { stroke:var(--accent); }
.edge.retry  { stroke:var(--accent-warm); }
.edge.resume { stroke:var(--ink-faint); stroke-dasharray:4 3; }
.edge.replay { stroke:var(--warn); stroke-dasharray:2 3; opacity:.7; }
.edge.rerun  { stroke:var(--violet); stroke-dasharray:2 3; opacity:.7; }
.dim { opacity:.18; }
.panel { width:0; border-left:1px solid var(--line); background:var(--panel);
  overflow:auto; transition:width .16s ease; }
.panel.open { width:380px; }
.panel .pad { padding:18px; width:380px; }
.panel h2 { margin:0 0 4px; font-size:13px; font-family:var(--mono);
  color:var(--accent); word-break:break-all; }
.panel .sub { color:var(--ink-dim); font-size:12px; margin-bottom:14px; }
.kv { display:grid; grid-template-columns:auto 1fr; gap:4px 12px;
  font-family:var(--mono); font-size:12px; font-variant-numeric:tabular-nums;
  margin-bottom:16px; }
.kv .k { color:var(--ink-faint); }
.kv .v { color:var(--ink); text-align:right; }
.pill { display:inline-block; padding:1px 8px; border-radius:10px; font-size:11px;
  font-family:var(--mono); }
.pill.completed { color:var(--ok); border:1px solid var(--ok); }
.pill.aborted { color:var(--bad); border:1px solid var(--bad); }
.pill.running { color:var(--warn); border:1px solid var(--warn); }
.pill.empty { color:var(--ink-faint); border:1px solid var(--line); }
.turn { border-top:1px solid var(--line-2); padding:10px 0; }
.turn .idx { color:var(--accent); font-family:var(--mono); font-size:11px; }
.turn .u { color:var(--ink); margin:4px 0; }
.turn .a { color:var(--ink-dim); font-size:13px; }
.turn .meta { color:var(--ink-faint); font-family:var(--mono); font-size:11px;
  margin-top:4px; }
.cmd { margin-top:14px; }
.cmd code { display:block; background:var(--bg); border:1px solid var(--line);
  border-radius:6px; padding:8px 10px; font-family:var(--mono); font-size:11px;
  color:var(--accent-warm); word-break:break-all; }
.cmd button { margin-top:6px; background:var(--panel); border:1px solid var(--line);
  color:var(--ink-dim); border-radius:6px; padding:4px 10px; font-size:11px;
  cursor:pointer; }
.cmd button:hover { border-color:var(--accent); color:var(--ink); }
.legend { display:flex; gap:14px; padding:8px 20px; border-top:1px solid var(--line);
  font-family:var(--mono); font-size:11px; color:var(--ink-dim); flex-wrap:wrap; }
.legend span::before { content:"—"; margin-right:5px; font-weight:700; }
.legend .branch::before { color:var(--accent); }
.legend .retry::before  { color:var(--accent-warm); }
.legend .resume::before { color:var(--ink-faint); }
.legend .replay::before { color:var(--warn); }
.legend .rerun::before  { color:var(--violet); }
.empty-state { padding:60px; color:var(--ink-faint); font-family:var(--mono); }
@media (prefers-reduced-motion:reduce){ *{transition:none!important;} }
"""

_SCRIPT = r"""
const FOREST = JSON.parse(document.getElementById("forest-data").textContent);
const COL_W = 130, ROW_H = 56, NODE_W = 104, NODE_H = 30, PAD_X = 150, PAD_Y = 30;

// Order sessions into rows: DFS pre-order from each root so children sit
// directly below their parent. Each session = one row (lane).
const byId = {}; FOREST.nodes.forEach(n => byId[n.sid] = n);
const childrenOf = {};
FOREST.edges.forEach(e => (childrenOf[e.parent_sid] ||= []).push(e));
const edgeToChild = {};
FOREST.edges.forEach(e => edgeToChild[e.child_sid] = e);

const rows = [];
function walk(sid, depth) {
  const n = byId[sid]; if (!n || n._placed) return;
  n._placed = true; n._row = rows.length; rows.push(n);
  (childrenOf[sid] || [])
    .sort((a,b) => (byId[a.child_sid]?.created_at||"").localeCompare(byId[b.child_sid]?.created_at||""))
    .forEach(e => walk(e.child_sid, depth+1));
}
FOREST.roots.forEach(sid => walk(sid, 0));
FOREST.nodes.forEach(n => { if (!n._placed) { n._row = rows.length; rows.push(n); } });

// x of a session's first turn: forks start under the parent's fork turn.
function laneStartCol(n) {
  const e = edgeToChild[n.sid];
  if (!e || !byId[e.parent_sid]) return 0;
  if (e.parent_turn != null) return byId[e.parent_sid]._startCol + e.parent_turn; // fork point
  return byId[e.parent_sid]._startCol + (byId[e.parent_sid].turn_count || 1);      // attach at end
}
rows.forEach(n => { n._startCol = 0; });
rows.forEach(n => { n._startCol = laneStartCol(n); });

function nodeX(n, turnIdx) { return PAD_X + (n._startCol + turnIdx) * COL_W; }
function nodeY(n) { return PAD_Y + n._row * ROW_H; }

const maxCol = Math.max(1, ...rows.map(n => n._startCol + Math.max(1, n.turn_count)));
const W = PAD_X + maxCol * COL_W + 40, H = PAD_Y + rows.length * ROW_H + 40;

const SVGNS = "http://www.w3.org/2000/svg";
function el(tag, attrs, parent) {
  const e = document.createElementNS(SVGNS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}
const svg = el("svg", {width:W, height:H, viewBox:`0 0 ${W} ${H}`});
document.querySelector(".canvas").appendChild(svg);

// edges first (under nodes)
FOREST.edges.forEach(e => {
  const p = byId[e.parent_sid], c = byId[e.child_sid];
  if (!p || !c) return;
  const pcol = e.parent_turn != null ? e.parent_turn - 1 : (p.turn_count - 1);
  const x1 = nodeX(p, Math.max(0, pcol)) + NODE_W/2, y1 = nodeY(p) + NODE_H;
  const x2 = nodeX(c, 0), y2 = nodeY(c) + NODE_H/2;
  const midY = (y1 + y2) / 2;
  const d = `M${x1},${y1} C${x1},${midY} ${x2-30},${y2} ${x2},${y2}`;
  el("path", {d, class:`edge ${e.kind}`}, svg);
});

// lanes + nodes
rows.forEach(n => {
  const label = el("text", {x:10, y:nodeY(n)+NODE_H/2+4,
    class:"lane-label" + (n.parent_missing ? " orphan":"")}, svg);
  label.textContent = (n.parent_missing ? "⚠ " : "") + shortSid(n.sid);

  const cols = Math.max(1, n.turn_count);
  for (let i=0; i<cols; i++) {
    const g = el("g", {class:`node ${n.status}`, "data-sid":n.sid, "data-turn":i+1}, svg);
    const x = nodeX(n, i), y = nodeY(n);
    el("rect", {x, y, width:NODE_W, height:NODE_H, rx:6}, g);
    const t = el("text", {x:x+8, y:y+NODE_H/2+3}, g);
    t.textContent = n.turns[i] ? ("t"+(i+1)+"  "+fmtTok(n.turns[i])) : ("t"+(i+1));
    g.addEventListener("click", () => select(n, i+1));
  }
});

function shortSid(s){ return s.length>13 ? s.slice(0,6)+"…"+s.slice(-4) : s; }
function fmtTok(t){ const k=(t.input_tokens+t.output_tokens); return k>=1000?(k/1000).toFixed(1)+"k":k+""; }
function fmtN(n){ return (n||0).toLocaleString(); }

// selection + detail panel
let selEl = null;
function select(n, turn) {
  document.querySelectorAll(".node.sel").forEach(e=>e.classList.remove("sel"));
  document.querySelectorAll(`.node[data-sid="${cssEsc(n.sid)}"]`).forEach(e=>e.classList.add("sel"));
  const t = n.turns[turn-1] || {};
  const panel = document.getElementById("panel");
  panel.classList.add("open");
  const lineage = describeLineage(n);
  panel.querySelector(".pad").innerHTML = `
    <h2>${esc(n.sid)}</h2>
    <div class="sub">${esc(n.provider)}/${esc(n.model)} · <span class="pill ${n.status}">${n.status}</span></div>
    <div class="kv">
      <span class="k">turns</span><span class="v">${n.turn_count}</span>
      <span class="k">tokens in</span><span class="v">${fmtN(n.input_tokens)}</span>
      <span class="k">tokens out</span><span class="v">${fmtN(n.output_tokens)}</span>
      ${lineage}
    </div>
    <div class="turn">
      <div class="idx">turn ${turn}</div>
      <div class="u">${esc(t.user_summary||"")}</div>
      <div class="a">${esc(t.assistant_summary||"")}</div>
      <div class="meta">${t.tool_calls||0} tool calls · ${fmtN((t.input_tokens||0)+(t.output_tokens||0))} tok</div>
    </div>
    <div class="cmd">
      <code id="branchcmd">/rewind ${turn}  (or: arc resume ${esc(n.sid)} --at-turn ${turn})</code>
      <button onclick="copyCmd()">copy branch command</button>
    </div>
    <div class="cmd"><a href="${esc(n.sid)}/session.html#turn-${turn}">open full session detail →</a></div>`;
}
function describeLineage(n){
  let r = "";
  if (n.resumed_from) { const k = n.retry_of_turn!=null?"retry of":(n.branched_at_turn!=null?"branched @ turn":"resumed from");
    r += `<span class="k">${k}</span><span class="v">${n.branched_at_turn??shortSid(n.resumed_from)}</span>`; }
  if (n.replay_of) r += `<span class="k">replay of</span><span class="v">${shortSid(n.replay_of)}</span>`;
  if (n.rerun_of) r += `<span class="k">rerun of</span><span class="v">${shortSid(n.rerun_of)}</span>`;
  if (n.provider_override) r += `<span class="k">model swap</span><span class="v">${esc(n.provider_override.model||"")}</span>`;
  if (n.parent_missing) r += `<span class="k">parent</span><span class="v" style="color:var(--warn)">missing</span>`;
  return r;
}
window.copyCmd = () => {
  const txt = document.getElementById("branchcmd").textContent;
  navigator.clipboard?.writeText(txt);
};
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function cssEsc(s){ return s.replace(/["\\]/g,"\\$&"); }

// filter: dim non-matching lanes
document.getElementById("filter").addEventListener("input", e => {
  const q = e.target.value.toLowerCase().trim();
  rows.forEach(n => {
    const hit = !q || n.sid.toLowerCase().includes(q) ||
      (n.provider+"/"+n.model).toLowerCase().includes(q) ||
      n.turns.some(t => (t.user_summary||"").toLowerCase().includes(q));
    document.querySelectorAll(`.node[data-sid="${cssEsc(n.sid)}"]`).forEach(g=>g.classList.toggle("dim", !hit));
  });
});
"""


def render_timeline_html(forest: Forest, *, title: str = "arc timeline") -> str:
    data = json.dumps(forest.to_dict(), ensure_ascii=False, separators=(",", ":"))
    n_sessions = len(forest.nodes)
    n_branches = sum(1 for e in forest.edges if e.kind in ("branch", "retry"))
    n_forks = len(forest.edges)
    tok = sum(n.input_tokens + n.output_tokens for n in forest.nodes)
    empty = "" if forest.nodes else '<div class="empty-state">no sessions recorded yet.</div>'
    legend = "".join(
        f'<span class="{k}">{k}</span>' for k in ("branch", "retry", "resume", "replay", "rerun")
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{_STYLE}</style></head>
<body><div class="app">
<header>
  <h1><span class="mark">arc</span> timeline</h1>
  <div class="stats">
    <span><b>{n_sessions}</b> sessions</span>
    <span><b>{n_branches}</b> branches</span>
    <span><b>{n_forks}</b> forks</span>
    <span><b>{_fmt_tok(tok)}</b> tokens</span>
  </div>
  <div class="filter"><input id="filter" type="text" placeholder="filter sid / model / text"></div>
</header>
<div class="body">
  <div class="canvas">{empty}</div>
  <div class="panel" id="panel"><div class="pad"></div></div>
</div>
<div class="legend">{legend}</div>
</div>
<script type="application/json" id="forest-data">{_escape_json_for_script(data)}</script>
<script>{_SCRIPT}</script>
</body></html>"""


def render_session_html(detail: dict[str, Any], node: dict[str, Any] | None = None) -> str:
    """Per-session detail page (phase b). `detail` from detail.build_session_detail."""
    sid = detail.get("sid", "?")
    prov = (node or {}).get("provider", "?")
    model = (node or {}).get("model", "?")
    turns_html = []
    for t in detail.get("turns", []):
        tools = "".join(
            f'<div class="tool"><div class="tname">→ {html.escape(str(tl.get("name","?")))}'
            f'({html.escape(_compact(tl.get("input")))})</div>'
            f'<pre class="tout">{html.escape(str(tl.get("output","")))}</pre></div>'
            for tl in t.get("tools", [])
        )
        thinking = (f'<pre class="thinking">{html.escape(t["thinking"])}</pre>'
                    if t.get("thinking") else "")
        turns_html.append(
            f'<section class="turn" id="turn-{t["index"]}">'
            f'<div class="idx">turn {t["index"]}</div>'
            f'<div class="u">{html.escape(t.get("user",""))}</div>'
            f'{thinking}{tools}'
            f'<div class="a">{html.escape(t.get("assistant",""))}</div>'
            f'</section>'
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(sid)} — arc session</title>
<style>{_STYLE}
.wrap{{max-width:900px;margin:0 auto;padding:24px;}}
.turn{{border-top:1px solid var(--line);padding:16px 0;}}
.turn .u{{color:var(--ink);font-weight:600;margin:6px 0;}}
.turn .a{{color:var(--ink-dim);white-space:pre-wrap;line-height:1.55;margin-top:8px;}}
.tool{{margin:8px 0;border-left:2px solid var(--line);padding-left:10px;}}
.tname{{color:var(--accent);font-family:var(--mono);font-size:12px;}}
.tout,.thinking{{background:var(--bg);border:1px solid var(--line-2);border-radius:6px;
 padding:8px 10px;font-family:var(--mono);font-size:11px;color:var(--ink-dim);
 white-space:pre-wrap;overflow-x:auto;max-height:340px;overflow-y:auto;margin:6px 0;}}
.thinking{{color:var(--violet);opacity:.85;}}
a.back{{font-family:var(--mono);font-size:12px;}}
</style></head>
<body><div class="wrap">
<a class="back" href="../timeline.html">← timeline</a>
<h1 style="font-family:var(--mono);color:var(--accent);word-break:break-all;">{html.escape(sid)}</h1>
<div class="sub" style="color:var(--ink-dim);margin-bottom:8px;">{html.escape(prov)}/{html.escape(model)}</div>
{"".join(turns_html) or '<p style="color:var(--ink-faint)">no turns recorded.</p>'}
</div></body></html>"""


# ── helpers ─────────────────────────────────────────────────────────────────


def _escape_json_for_script(s: str) -> str:
    # `</script>` inside embedded JSON would close the tag early — the classic
    # embed bug. Break the sequence without changing the parsed value.
    return s.replace("</", "<\\/")


def _fmt_tok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def _compact(v: Any) -> str:
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= 80 else s[:79] + "…"
