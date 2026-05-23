"""The agent session + ReAct loop.

This is the core of the runtime. A single class — AgentSession — that:
  - Holds conversation state across turns
  - Calls the provider, dispatches tools, fires hooks at the right moments
  - Emits canonical events through the bus
  - Respects iteration / tool-call caps from config
  - Handles cooperative pause/cancel via pause_check hook

Design rules:
  - The loop is the ONLY thing that emits llm.* and tool.* events
  - Hooks are fired via the registry; failures isolated by the registry
  - Identity (session_id, turn_id, scope, parent_event_id) flows through contextvars
  - Tool results are appended to the conversation and the loop continues
  - When the LLM ends a turn with no tool calls, the loop exits

Target: keep this file readable in one pass. Currently ~300 lines.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from arc.config import Config
from arc.providers.base import LLMProvider
from arc.runtime.bus import EventBus, HookRegistry
from arc.runtime.events import EventType, RuntimeEvent, Severity
from arc.runtime.hooks import (
    Cancelled,
    ContentBlock,
    LLMRequest,
    LLMResponse,
    Message,
    PauseRequested,
    SessionContext,
    ToolCall,
    ToolDenial,
    ToolResult,
    ToolSpec,
    TurnContext,
    TurnOutcome,
    UserInput,
)
from arc.runtime.ids import new_session_id, new_tool_call_id, new_turn_id
from arc.runtime.scope import parent_event, session, turn
from arc.tools.base import ToolError, ToolRegistry


@dataclass
class AgentSession:
    """One session with a single agent. Holds conversation across turns.

    Build via the module-level `build()` from Config; use directly in tests.
    """

    config: Config
    provider: LLMProvider
    tools: ToolRegistry
    registry: HookRegistry
    bus: EventBus

    session_id: str = ""
    # Pre-loaded conversation. Used by `arc resume` to start a session
    # with prior context already in place. Left empty for fresh sessions.
    initial_messages: list[Message] | None = None
    _messages: list[Message] = None  # type: ignore[assignment]
    _session_ctx: SessionContext | None = None
    _started: bool = False
    _last_outcome: TurnOutcome | None = None

    def __post_init__(self) -> None:
        if not self.session_id:
            self.session_id = new_session_id()
        if self._messages is None:
            self._messages = list(self.initial_messages) if self.initial_messages else []

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> SessionContext:
        """Initialize the session. Idempotent — safe to call multiple times.

        Ordering:
          1. Fire on_session_start FIRST so plugins (recorder, plus session-
             scoped plugins like briefbot) can set up before any events fire.
          2. Merge plugin-contributed tools into the registry (provides_tools).
             Plugins build these in on_session_start, so the merge MUST happen
             after step 1.
          3. Bind the event bus to any tool that declares bind_bus(bus).
          4. Emit session.started — that event lands in the now-ready event
             log with the *final* tool list (built-in + plugin-contributed).
        """
        if self._started:
            return self._session_ctx  # type: ignore[return-value]

        self._session_ctx = SessionContext(
            session_id=self.session_id,
            workspace=self.config.runtime.workspace,
            provider_name=self.config.provider.name,
            provider_model=self.config.provider.model,
            started_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        )
        self.bus.set_session_context(self._session_ctx)

        with session(self.session_id):
            self.registry.fire_observer("on_session_start", ctx=self._session_ctx)

            # Merge plugin-contributed tools (no-op if no plugin defines
            # provides_tools). Collisions raise — this is loud on purpose.
            self._merge_plugin_tools()

            # Bind the bus to tools that need it (optional contract).
            self._bind_bus_to_tools()

            self.bus.emit(RuntimeEvent(
                type=EventType.SESSION_STARTED,
                stage="AgentSession",
                payload={
                    "provider": self.config.provider.name,
                    "model": self.config.provider.model,
                    "workspace": self.config.runtime.workspace,
                    "tools": self.tools.names(),
                },
            ))

        self._started = True
        return self._session_ctx

    # ── Internal: tool merge + bus binding ────────────────────────────────

    def _merge_plugin_tools(self) -> None:
        """Ask each registered plugin (via the hook registry) for tools to
        contribute, register them, and emit an observability event.

        We pull plugins from the registry rather than receiving them as a
        constructor arg so the merge stays close to the lifecycle ordering.
        """
        plugins = list(getattr(self.registry, "iter_plugins", lambda: [])())
        added: list[str] = []
        for plugin in plugins:
            provider = getattr(plugin, "provides_tools", None)
            if not callable(provider):
                continue
            try:
                tools = list(provider() or [])
            except Exception as exc:  # noqa: BLE001 — surface, don't crash
                self.bus.emit(RuntimeEvent(
                    type=EventType.PLUGIN_HOOK_FAILED,
                    stage="AgentSession",
                    severity=Severity.ERROR,
                    payload={
                        "plugin": getattr(plugin, "name", type(plugin).__name__),
                        "hook": "provides_tools",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                ))
                continue
            for tool in tools:
                if tool.name in self.tools:
                    raise ValueError(
                        f"plugin {getattr(plugin, 'name', type(plugin).__name__)!r} "
                        f"provides tool {tool.name!r} but a tool with that name "
                        f"is already registered"
                    )
                self.tools.register(tool)
                added.append(tool.name)
        if added:
            self.bus.emit(RuntimeEvent(
                type=EventType.PLUGIN_TOOLS_REGISTERED,
                stage="AgentSession",
                payload={"tools": added},
            ))

    def _bind_bus_to_tools(self) -> None:
        """Call bind_bus(bus) on every tool that defines it. Tools that emit
        structured events declare this method; tools that don't (the boring
        majority) silently skip the call.
        """
        for tool in self.tools.all():
            binder = getattr(tool, "bind_bus", None)
            if callable(binder):
                binder(self.bus)

    def end(self) -> None:
        """Finalize the session.

        Ordering: emit session.ended FIRST so it appears in the event log,
        THEN fire on_session_end so plugins (recorder) can do final writes
        (meta.json stamping, index append) AFTER the final event is logged.
        """
        if not self._started:
            return
        with session(self.session_id):
            self.bus.emit(RuntimeEvent(
                type=EventType.SESSION_ENDED,
                stage="AgentSession",
                payload={
                    "n_messages": len(self._messages),
                },
            ))
            self.registry.fire_observer(
                "on_session_end",
                ctx=self._session_ctx,
                outcome=self._last_outcome,
            )
        self._started = False

    # ── Turn execution ───────────────────────────────────────────────────

    def run_turn(self, user_input_text: str) -> TurnOutcome:
        """Run one ReAct turn. Blocks until the model ends the turn or a cap fires."""
        if not self._started:
            self.start()

        turn_id = new_turn_id()
        with session(self.session_id), turn(turn_id):
            outcome = self._run_turn_inner(turn_id, user_input_text)
            self._last_outcome = outcome
            return outcome

    def _run_turn_inner(self, turn_id: str, user_input_text: str) -> TurnOutcome:
        """The actual loop, run under session+turn scope."""
        # Build turn context (initial — iteration starts at 0)
        turn_ctx = TurnContext(
            session=self._session_ctx,  # type: ignore[arg-type]
            turn_id=turn_id,
            user_input=user_input_text,
            iteration=0,
        )

        # 1. on_turn_start — plugins can rewrite the user input
        user_in: UserInput = self.registry.fire(
            "on_turn_start",
            UserInput(text=user_input_text),
            ctx=turn_ctx,
        )

        # 2. Append user message + emit turn.started
        self._messages.append(Message(role="user", content=user_in.text))
        self.bus.emit(RuntimeEvent(
            type=EventType.TURN_STARTED,
            stage="AgentSession",
            payload={"turn_id": turn_id},
            content={"user_input": user_in.text},
        ))

        # 3. ReAct loop
        iteration = 0
        n_tool_calls = 0
        n_llm_calls = 0
        final_text = ""
        error_msg: str | None = None

        cap_iter = self.config.runtime.max_iterations
        cap_tools = self.config.runtime.max_tool_calls_per_turn

        # Cycle detection: track recent tool-call signatures so we can detect
        # when the model is looping on the same failed call. Window holds
        # the last N signatures; if the most recent `threshold` are all
        # identical, we force a wrap-up.
        cycle_threshold = self.config.runtime.cycle_detection_threshold
        from collections import deque
        recent_sigs: deque[tuple] = deque(maxlen=max(cycle_threshold * 2, 8))

        try:
            while True:
                iteration += 1
                turn_ctx = _bump_iteration(turn_ctx, iteration)

                # Cap: iteration
                if iteration > cap_iter:
                    self._messages.append(Message(
                        role="user",
                        content=self.config.runtime.iteration_cap_message,
                    ))
                    final_text = self._force_wrap_up(turn_ctx)
                    n_llm_calls += 1
                    break

                # Cycle detection — fire BEFORE the next LLM call so we can
                # short-circuit the next round of identical tool dispatch.
                if _is_period_1_cycle(recent_sigs, cycle_threshold):
                    self.bus.emit(RuntimeEvent(
                        type=EventType.RUNTIME_CYCLE_DETECTED,
                        stage="AgentSession",
                        severity=Severity.WARN,
                        payload={
                            "threshold": cycle_threshold,
                            "signature": list(recent_sigs)[-1],
                        },
                    ))
                    self._messages.append(Message(
                        role="user",
                        content=self.config.runtime.cycle_detected_message,
                    ))
                    final_text = self._force_wrap_up(turn_ctx)
                    n_llm_calls += 1
                    error_msg = "cycle"
                    break

                # Cooperative pause point
                self.registry.fire_observer("pause_check", ctx=turn_ctx)

                # 3a. pack_context — plugins can compress/filter messages
                packed = self.registry.fire(
                    "pack_context",
                    list(self._messages),
                    ctx=turn_ctx,
                    query=user_in.text,
                )

                # 3b. Build LLMRequest, fire before_llm_call
                req = LLMRequest(
                    messages=packed,
                    system=self.config.runtime.system_prompt,
                    tools=self._tool_specs(),
                    model=self.config.provider.model,
                    params=dict(self.config.provider.params),
                )
                req = self.registry.fire("before_llm_call", req, ctx=turn_ctx)

                # 3c. Emit llm.call.started (canonical bytes for replay)
                llm_started = RuntimeEvent(
                    type=EventType.LLM_CALL_STARTED,
                    stage="AgentSession",
                    payload={
                        "provider": self.config.provider.name,
                        "model": req.model,
                        "message_count": len(req.messages),
                        "tool_count": len(req.tools),
                    },
                    content={
                        "messages": [_message_to_dict(m) for m in req.messages],
                        "system": req.system,
                        "tools": [_tool_spec_to_dict(t) for t in req.tools],
                        "params": req.params,
                    },
                )
                self.bus.emit(llm_started)

                # 3d. Call provider; nest child events under this llm.call
                try:
                    with parent_event(llm_started.event_id):
                        resp = self.provider.chat(req)
                except Exception as exc:
                    self.bus.emit(RuntimeEvent(
                        type=EventType.LLM_CALL_FAILED,
                        stage="AgentSession",
                        severity=Severity.ERROR,
                        parent_event_id=llm_started.event_id,
                        payload={"exception_type": type(exc).__name__,
                                 "exception_message": str(exc)[:500]},
                    ))
                    error_msg = f"provider call failed: {exc}"
                    break

                n_llm_calls += 1

                # 3e. Emit llm.call.completed
                self.bus.emit(RuntimeEvent(
                    type=EventType.LLM_CALL_COMPLETED,
                    stage="AgentSession",
                    parent_event_id=llm_started.event_id,
                    payload={
                        "stop_reason": resp.stop_reason,
                        "input_tokens": resp.input_tokens,
                        "output_tokens": resp.output_tokens,
                    },
                    content={
                        "response_content": [_block_to_dict(b) for b in resp.content],
                        "raw_provider_response": resp.raw,
                    },
                ))

                # 3f. after_llm_call
                resp = self.registry.fire(
                    "after_llm_call",
                    resp,
                    ctx=turn_ctx,
                    req=req,
                )

                # 3g. Append assistant message to conversation
                self._messages.append(Message(
                    role="assistant",
                    content=list(resp.content),
                ))

                # 3h. Tool calls — if none, the turn is done
                tool_uses = [b for b in resp.content if b.type == "tool_use"]
                text_blocks = [b for b in resp.content if b.type == "text" and b.text]
                final_text = "".join(b.text for b in text_blocks if b.text)

                if not tool_uses:
                    break

                # 3i. Dispatch tools
                for block in tool_uses:
                    if n_tool_calls >= cap_tools:
                        self._messages.append(Message(
                            role="user",
                            content=self.config.runtime.tool_call_cap_message,
                        ))
                        # Let next iteration prompt the model to wrap up
                        break

                    call = ToolCall(
                        tool_call_id=block.tool_use_id or new_tool_call_id(),
                        name=block.tool_name or "",
                        input=dict(block.tool_input or {}),
                    )

                    # Track signature for cycle detection. Use sorted-keys
                    # JSON of the input so equivalent dicts canonicalize.
                    recent_sigs.append(_call_signature(call))

                    # before_tool_call — may return ToolDenial
                    decision = self.registry.fire(
                        "before_tool_call",
                        call,
                        ctx=turn_ctx,
                    )

                    if isinstance(decision, ToolDenial):
                        tool_result = self._handle_denial(decision)
                    else:
                        tool_result = self._execute_tool(decision, llm_started.event_id)

                    n_tool_calls += 1

                    # after_tool_call
                    tool_result = self.registry.fire(
                        "after_tool_call",
                        tool_result,
                        ctx=turn_ctx,
                        call=call,
                    )

                    # Append tool result to the conversation as a "tool" role message.
                    # Gemini accepts function_response dicts inside the parts list.
                    self._messages.append(Message(
                        role="tool",
                        content=[{
                            "function_response": {
                                "name": call.name,
                                "response": {"result": tool_result.output},
                            }
                        }],
                        name=call.name,
                    ))

        except PauseRequested:
            error_msg = "paused"
        except Cancelled:
            error_msg = "cancelled"

        # 4. Build outcome
        outcome = TurnOutcome(
            success=error_msg is None,
            final_response=final_text,
            n_tool_calls=n_tool_calls,
            n_llm_calls=n_llm_calls,
            error=error_msg,
        )

        # 5. Emit turn.ended and fire on_turn_end
        self.bus.emit(RuntimeEvent(
            type=EventType.TURN_ENDED,
            stage="AgentSession",
            payload={
                "success": outcome.success,
                "n_tool_calls": outcome.n_tool_calls,
                "n_llm_calls": outcome.n_llm_calls,
                "error": outcome.error,
            },
            content={"final_response": outcome.final_response},
        ))
        self.registry.fire_observer("on_turn_end", ctx=turn_ctx, outcome=outcome)
        return outcome

    # ── Helpers ──────────────────────────────────────────────────────────

    def _tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name=t.name,
                description=t.description,
                input_schema=t.input_schema.to_json_schema(),
            )
            for t in self.tools.all()
        ]

    def _execute_tool(self, call: ToolCall, parent_id: str) -> ToolResult:
        """Run a single tool. Emits started/completed/failed events."""
        started = RuntimeEvent(
            type=EventType.TOOL_CALL_STARTED,
            stage="AgentSession",
            parent_event_id=parent_id,
            payload={"tool_name": call.name, "tool_call_id": call.tool_call_id},
            content={"input": dict(call.input)},
        )
        self.bus.emit(started)

        try:
            with parent_event(started.event_id):
                output = self.tools.get(call.name).execute(call.input)
            tool_result = ToolResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                ok=True,
                output=output,
            )
            self.bus.emit(RuntimeEvent(
                type=EventType.TOOL_CALL_COMPLETED,
                stage="AgentSession",
                parent_event_id=started.event_id,
                payload={
                    "tool_name": call.name,
                    "tool_call_id": call.tool_call_id,
                    "ok": True,
                    "output_bytes": len(output.encode("utf-8", errors="replace")),
                },
                content={"output": output},
            ))
            return tool_result

        except ToolError as e:
            return self._tool_failed(call, started.event_id, str(e), "tool_error")
        except KeyError as e:
            return self._tool_failed(call, started.event_id, f"unknown tool: {e}", "tool_unknown")
        except Exception as e:
            return self._tool_failed(call, started.event_id,
                                     f"{type(e).__name__}: {e}", "unexpected")

    def _tool_failed(
        self, call: ToolCall, parent_id: str, msg: str, code: str,
    ) -> ToolResult:
        self.bus.emit(RuntimeEvent(
            type=EventType.TOOL_CALL_FAILED,
            stage="AgentSession",
            severity=Severity.ERROR,
            parent_event_id=parent_id,
            payload={
                "tool_name": call.name,
                "tool_call_id": call.tool_call_id,
                "error_code": code,
                "error_message": msg[:500],
            },
        ))
        return ToolResult(
            tool_call_id=call.tool_call_id,
            name=call.name,
            ok=False,
            output=f"Error: {msg}",
            error_code=code,
        )

    def _handle_denial(self, denial: ToolDenial) -> ToolResult:
        self.bus.emit(RuntimeEvent(
            type=EventType.TOOL_CALL_DENIED,
            stage="AgentSession",
            severity=Severity.WARN,
            payload={
                "tool_name": denial.name,
                "tool_call_id": denial.tool_call_id,
                "reason": denial.reason[:500],
            },
        ))
        return ToolResult(
            tool_call_id=denial.tool_call_id,
            name=denial.name,
            ok=False,
            output=f"Tool call denied: {denial.reason}",
            error_code="denied",
        )

    def _force_wrap_up(self, turn_ctx: TurnContext) -> str:
        """One final LLM call with no tools available — pure synthesis."""
        req = LLMRequest(
            messages=list(self._messages),
            system=self.config.runtime.system_prompt,
            tools=[],  # no tools — model is forced to write prose
            model=self.config.provider.model,
            params=dict(self.config.provider.params),
        )
        wrap_started = RuntimeEvent(
            type=EventType.LLM_CALL_STARTED,
            stage="AgentSession",
            payload={"wrap_up": True, "model": req.model},
            content={
                "messages": [_message_to_dict(m) for m in req.messages],
                "system": req.system,
                "tools": [],
                "params": req.params,
            },
        )
        self.bus.emit(wrap_started)
        try:
            resp = self.provider.chat(req)
        except Exception as exc:
            self.bus.emit(RuntimeEvent(
                type=EventType.LLM_CALL_FAILED,
                stage="AgentSession",
                severity=Severity.ERROR,
                parent_event_id=wrap_started.event_id,
                payload={"exception_type": type(exc).__name__,
                         "exception_message": str(exc)[:500]},
            ))
            return ""

        self.bus.emit(RuntimeEvent(
            type=EventType.LLM_CALL_COMPLETED,
            stage="AgentSession",
            parent_event_id=wrap_started.event_id,
            payload={"stop_reason": resp.stop_reason,
                     "input_tokens": resp.input_tokens,
                     "output_tokens": resp.output_tokens},
            content={
                "response_content": [_block_to_dict(b) for b in resp.content],
                "raw_provider_response": resp.raw,
            },
        ))

        text = "".join(b.text for b in resp.content if b.type == "text" and b.text)
        self._messages.append(Message(role="assistant", content=list(resp.content)))
        return text


# ── Free helpers ───────────────────────────────────────────────────────────


def _bump_iteration(ctx: TurnContext, n: int) -> TurnContext:
    """Frozen dataclass — produce a new instance with updated iteration."""
    from dataclasses import replace
    return replace(ctx, iteration=n)


def _call_signature(call: ToolCall) -> tuple:
    """Stable hashable signature for a tool call. Used by cycle detection.

    Canonicalizes input via sort_keys so equivalent dicts compare equal
    regardless of key order.
    """
    import json
    canonical_input = json.dumps(call.input, sort_keys=True, ensure_ascii=False,
                                 separators=(",", ":"))
    return (call.name, canonical_input)


def _is_period_1_cycle(sigs, threshold: int) -> bool:
    """True if the last `threshold` signatures are all identical.

    Period-1 cycle = same tool called with same input N times in a row.
    Most common failure mode for confused models. We can add period-2
    (A-B-A-B) detection later if it shows up in practice.
    """
    if threshold <= 1 or len(sigs) < threshold:
        return False
    last = sigs[-1]
    return all(s == last for s in list(sigs)[-threshold:])


def _message_to_dict(m: Message) -> dict[str, Any]:
    """Canonical dict form of a Message. Plain dicts, no class names."""
    content: Any
    if isinstance(m.content, str):
        content = m.content
    else:
        content = [_block_to_dict(b) if isinstance(b, ContentBlock) else b
                   for b in m.content]
    out: dict[str, Any] = {"role": m.role, "content": content}
    if m.name is not None:
        out["name"] = m.name
    return out


def _block_to_dict(b: ContentBlock) -> dict[str, Any]:
    """Canonical dict form of a ContentBlock. Only non-null fields."""
    out: dict[str, Any] = {"type": b.type}
    if b.text is not None:
        out["text"] = b.text
    if b.tool_use_id is not None:
        out["tool_use_id"] = b.tool_use_id
    if b.tool_name is not None:
        out["tool_name"] = b.tool_name
    if b.tool_input is not None:
        out["tool_input"] = b.tool_input
    if b.metadata is not None:
        # Metadata may contain bytes (e.g., Gemini thought_signature) — base64
        # them for JSON-safety. Round-trips cleanly during replay.
        import base64
        safe_meta = {}
        for k, v in b.metadata.items():
            if isinstance(v, bytes):
                safe_meta[k] = {"__bytes_b64__": base64.b64encode(v).decode("ascii")}
            else:
                safe_meta[k] = v
        out["metadata"] = safe_meta
    return out


def _tool_spec_to_dict(t: ToolSpec) -> dict[str, Any]:
    return {
        "name": t.name,
        "description": t.description,
        "input_schema": t.input_schema,
    }
