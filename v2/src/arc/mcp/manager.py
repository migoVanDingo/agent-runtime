"""McpManager — owns MCP connections for a session and bridges async↔sync.

arc's runtime is synchronous; the `mcp` SDK is async (anyio). The manager runs a
**dedicated asyncio loop in a background thread**. Each server gets a long-lived
**actor coroutine** that opens the connection, then serves `call_tool` requests
from a queue, and closes — all within one task. Opening/using/closing in the
same task sidesteps anyio's "cancel scope in a different task" pitfalls. Sync
callers reach the loop via `run_coroutine_threadsafe(...).result(timeout)`.

Per-server failure isolation: one server failing to connect / crashing / erroring
disables only itself (state → errored/quarantined, event emitted); other servers
keep serving. Failures never propagate out to arc's whole-plugin quarantine.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any

from arc.mcp.config import McpConfig, McpServerConfig
from arc.mcp.transport import McpSdkMissing, open_transport
from arc.runtime.events import EventType, RuntimeEvent, Severity

_SHUTDOWN = object()  # queue sentinel: actor should close its connection and exit

# States a server connection can be in.
DISCONNECTED = "disconnected"
CONNECTED = "connected"
ERRORED = "errored"
QUARANTINED = "quarantined"


@dataclass
class ToolDef:
    """A discovered MCP tool (server-scoped), decoupled from the SDK types."""
    server: str
    name: str
    description: str
    input_schema: dict


@dataclass
class _ServerConn:
    cfg: McpServerConfig
    state: str = DISCONNECTED
    strikes: int = 0
    error: str = ""
    tools: list[ToolDef] = field(default_factory=list)
    queue: Any = None            # asyncio.Queue, created on the loop
    actor: Any = None            # asyncio.Task
    connected_evt: Any = None    # asyncio.Future resolved when initialize() done


class McpCallError(RuntimeError):
    """A tool call failed (server errored, timed out, or returned isError)."""


class McpManager:
    def __init__(self, cfg: McpConfig, *, bus: Any = None) -> None:
        self._cfg = cfg
        self._bus = bus
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._conns: dict[str, _ServerConn] = {}

    # ── event helper ─────────────────────────────────────────────────────────

    def _emit(self, etype: str, payload: dict, *, severity: str = Severity.INFO) -> None:
        if self._bus is None:
            return
        self._bus.emit(RuntimeEvent(type=etype, payload=payload, stage="plugin", severity=severity))

    # ── loop lifecycle ───────────────────────────────────────────────────────

    def _start_loop(self) -> None:
        if self._loop is not None:
            return
        loop = asyncio.new_event_loop()
        loop.set_exception_handler(_quiet_teardown)
        ready = threading.Event()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            ready.set()
            loop.run_forever()

        t = threading.Thread(target=_run, name="arc-mcp-loop", daemon=True)
        t.start()
        ready.wait()
        self._loop, self._thread = loop, t

    def _submit(self, coro, timeout: float | None):
        assert self._loop is not None
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout)

    # ── connect / discover ───────────────────────────────────────────────────

    def connect_all(self, *, connect_timeout: float = 20.0) -> None:
        """Connect every enabled server. Per-server isolation — a bad server is
        marked errored and skipped; the rest still connect."""
        active = self._cfg.active()
        self._emit(
            EventType.MCP_SERVERS_CONFIGURED,
            {"enabled_count": len(active), "total": len(self._cfg.servers),
             "servers": [s.name for s in active]},
        )
        if not active:
            return
        self._start_loop()
        for cfg in active:
            conn = _ServerConn(cfg=cfg)
            self._conns[cfg.name] = conn
            try:
                self._submit(self._aconnect(conn), connect_timeout)
            except McpSdkMissing as exc:
                conn.state, conn.error = ERRORED, str(exc)
                self._emit(EventType.MCP_SERVER_ERROR,
                           {"server": cfg.name, "error": str(exc)}, severity=Severity.WARN)
            except Exception as exc:  # noqa: BLE001 — isolate this server only
                conn.state, conn.error = ERRORED, _exc_str(exc)
                self._emit(EventType.MCP_SERVER_ERROR,
                           {"server": cfg.name, "error": conn.error}, severity=Severity.WARN)
                continue
            conn.state = CONNECTED
            self._emit(EventType.MCP_SERVER_CONNECTED,
                       {"server": cfg.name, "transport": cfg.transport,
                        "tool_count": len(conn.tools)})
            self._emit(EventType.MCP_TOOLS_DISCOVERED,
                       {"server": cfg.name, "tools": [t.name for t in conn.tools]})

    async def _aconnect(self, conn: _ServerConn) -> None:
        """Create the actor task and wait until it has initialized + listed tools."""
        loop = asyncio.get_running_loop()
        conn.queue = asyncio.Queue()
        conn.connected_evt = loop.create_future()
        conn.actor = asyncio.create_task(self._actor(conn))
        await conn.connected_evt  # raises if the actor failed before initialize

    async def _actor(self, conn: _ServerConn) -> None:
        """Own the connection for its whole life within this single task."""
        try:
            async with open_transport(conn.cfg) as streams:
                from mcp import ClientSession

                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    conn.tools = [
                        ToolDef(server=conn.cfg.name, name=t.name,
                                description=t.description or "",
                                input_schema=dict(t.inputSchema or {"type": "object"}))
                        for t in listed.tools
                    ]
                    if not conn.connected_evt.done():
                        conn.connected_evt.set_result(True)
                    await self._serve(conn, session)
        except Exception as exc:  # noqa: BLE001 — surface to the connect awaiter / mark errored
            if conn.connected_evt is not None and not conn.connected_evt.done():
                conn.connected_evt.set_exception(exc)
            else:
                conn.state, conn.error = ERRORED, _exc_str(exc)

    async def _serve(self, conn: _ServerConn, session: Any) -> None:
        while True:
            req = await conn.queue.get()
            if req is _SHUTDOWN:
                return
            name, arguments, fut = req
            try:
                result = await session.call_tool(name, arguments or {})
                if not fut.done():
                    fut.set_result(result)
            except Exception as exc:  # noqa: BLE001 — return to the caller, keep serving
                if not fut.done():
                    fut.set_exception(exc)

    # ── calls ────────────────────────────────────────────────────────────────

    def call_tool(self, server: str, tool: str, arguments: dict | None) -> dict:
        """Synchronous tool call. Returns {text, is_error, structured}."""
        conn = self._conns.get(server)
        if conn is None or conn.state not in (CONNECTED,):
            raise McpCallError(
                f"mcp server {server!r} is not connected "
                f"({conn.state if conn else 'unknown'})"
            )
        self._emit(EventType.MCP_TOOL_CALLED, {"server": server, "tool": tool})
        try:
            raw = self._submit(self._acall(conn, tool, arguments), self._cfg.call_timeout_s + 5)
        except Exception as exc:  # noqa: BLE001
            self._record_strike(conn, f"call {tool}: {exc}")
            raise McpCallError(f"mcp {server}.{tool} failed: {exc}") from exc
        out = _flatten_result(raw)
        self._emit(EventType.MCP_TOOL_RESULT,
                   {"server": server, "tool": tool, "is_error": out["is_error"],
                    "bytes": len(out["text"])},
                   severity=Severity.WARN if out["is_error"] else Severity.INFO)
        return out

    async def _acall(self, conn: _ServerConn, tool: str, arguments: dict | None):
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        await conn.queue.put((tool, arguments, fut))
        return await asyncio.wait_for(fut, timeout=self._cfg.call_timeout_s)

    def _record_strike(self, conn: _ServerConn, reason: str) -> None:
        conn.strikes += 1
        conn.error = reason
        if conn.strikes >= self._cfg.failure_threshold:
            conn.state = QUARANTINED
            self._emit(EventType.MCP_SERVER_QUARANTINED,
                       {"server": conn.cfg.name, "strikes": conn.strikes, "reason": reason},
                       severity=Severity.WARN)

    # ── discovery accessors ──────────────────────────────────────────────────

    def all_tools(self) -> list[ToolDef]:
        out: list[ToolDef] = []
        for conn in self._conns.values():
            if conn.state != CONNECTED:
                continue
            for t in conn.tools:
                if _tool_allowed(conn.cfg, t.name):
                    out.append(t)
        return out

    def status(self) -> list[dict]:
        rows = []
        for cfg in self._cfg.servers:
            conn = self._conns.get(cfg.name)
            rows.append({
                "name": cfg.name, "transport": cfg.transport, "enabled": cfg.enabled,
                "state": conn.state if conn else DISCONNECTED,
                "tool_count": len(conn.tools) if conn else 0,
                "error": conn.error if conn else "",
            })
        return rows

    # ── shutdown ─────────────────────────────────────────────────────────────

    def disconnect_all(self, *, timeout: float = 10.0) -> None:
        if self._loop is None:
            return
        for conn in self._conns.values():
            if conn.actor is not None:
                try:
                    self._submit(self._ashutdown(conn), timeout)
                except Exception:  # noqa: BLE001 — best-effort teardown
                    pass
                self._emit(EventType.MCP_SERVER_DISCONNECTED, {"server": conn.cfg.name})
        loop = self._loop
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._loop, self._thread = None, None
        self._conns.clear()

    async def _ashutdown(self, conn: _ServerConn) -> None:
        if conn.queue is not None:
            await conn.queue.put(_SHUTDOWN)
        if conn.actor is not None:
            try:
                await asyncio.wait_for(conn.actor, timeout=5.0)
            except Exception:  # noqa: BLE001
                conn.actor.cancel()


# ── helpers ──────────────────────────────────────────────────────────────────


def _quiet_teardown(loop, context) -> None:
    """Background-loop exception handler. Swallows the pipe/subprocess teardown
    noise anyio raises in internal tasks when a stdio server exits (we already
    capture meaningful failures on the connect/call futures + events). Real,
    unexpected loop errors still fall through to the default handler."""
    exc = context.get("exception")
    if isinstance(exc, (BrokenPipeError, ConnectionResetError, ProcessLookupError,
                        asyncio.CancelledError)):
        return
    loop.default_exception_handler(context)


def _exc_str(exc: BaseException) -> str:
    """Readable one-liner, unwrapping anyio/asyncio ExceptionGroups to a leaf."""
    inner = getattr(exc, "exceptions", None)
    if inner:
        return _exc_str(inner[0])
    return f"{type(exc).__name__}: {exc}"


def _tool_allowed(cfg: McpServerConfig, name: str) -> bool:
    if cfg.tools_deny and name in cfg.tools_deny:
        return False
    if cfg.tools_allow:
        return name in cfg.tools_allow
    return True


def _flatten_result(raw: Any) -> dict:
    """CallToolResult -> {text, is_error, structured}. Joins text content blocks."""
    is_error = bool(getattr(raw, "isError", False))
    structured = getattr(raw, "structuredContent", None)
    parts: list[str] = []
    for block in getattr(raw, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
        else:
            btype = getattr(block, "type", "content")
            parts.append(f"[{btype} content]")
    return {"text": "\n".join(parts), "is_error": is_error, "structured": structured}
