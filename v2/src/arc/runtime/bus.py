"""Hook registry + event bus.

The runtime owns one registry and one bus per session. The registry holds
plugins and exposes typed dispatchers (`fire(hook_name, ...)`). The bus emits
events to whatever plugins implement `on_event`.

Per design §5:
  - Composition order is config-specified (lower number = earlier)
  - Plugins are NOT auto-discovered — the runtime instantiates them explicitly
  - Failures in hooks are caught, emitted as events, and the chain continues
    using the pre-hook value. Exception: PauseRequested/Cancelled propagate.
  - Repeated failures (3 per session per plugin) disable that plugin for the
    rest of the session.
"""
from __future__ import annotations

import traceback
from collections import defaultdict
from typing import Any

from arc.runtime.events import EventType, RuntimeEvent, Severity
from arc.runtime.hooks import (
    ALL_HOOK_NAMES,
    Cancelled,
    PauseRequested,
)


class HookRegistry:
    """Holds plugins and their per-hook composition order.

    Lifecycle:
        reg = HookRegistry(failure_threshold=..., exception_message_max_chars=...)
        reg.register(plugin_instance, hooks_order={"on_event": 100, ...})
        reg.fire("before_llm_call", req, ctx=ctx)  # threads return value

    All tunable thresholds are passed in by the caller — typically from
    `config.plugins.*`. No hardcoded defaults: per the no-hardcoded-defaults
    principle in design §3, every user-tunable lives in config.yml.
    """

    def __init__(
        self,
        *,
        failure_threshold: int,
        exception_message_max_chars: int,
    ) -> None:
        # hook_name -> list of (priority, plugin_name, callable)
        # sorted by priority on register
        self._chains: dict[str, list[tuple[int, str, Any]]] = defaultdict(list)
        # plugin_name -> failure count (for auto-disable)
        self._failures: dict[str, int] = defaultdict(int)
        # plugin_name -> set of hook names the plugin was disabled FROM
        self._disabled: set[str] = set()
        # plugin_name -> instance, for `iter_plugins()` lookups. Lets the
        # runtime ask "what plugins are registered?" without walking chains.
        self._plugins: dict[str, Any] = {}
        self._threshold = failure_threshold
        self._exc_msg_max = exception_message_max_chars
        # Set by the runtime so failed-hook events can be emitted onto the bus
        self._bus: EventBus | None = None

    def bind_bus(self, bus: EventBus) -> None:
        """Wire the registry to the bus so it can emit plugin.* events."""
        self._bus = bus

    def iter_plugins(self) -> list[Any]:
        """Return the set of unique plugin instances currently registered.

        Used by the runtime to ask plugins for tools (provides_tools) without
        coupling to the chains structure. Returns instances, not names —
        callers commonly want to dispatch methods on the plugin object.
        """
        return list(self._plugins.values())

    def register(self, plugin: Any, *, hooks_order: dict[str, int]) -> None:
        """Register a plugin against named hooks with priorities.

        The plugin must implement methods matching the hook names in
        `hooks_order` — registering for a hook the plugin doesn't implement
        is a programmer error and raises at registration time.

        A plugin may be registered with `hooks_order={}` — useful for plugins
        that contribute tools via `provides_tools()` but implement no hooks.
        In that case the instance is still tracked in `_plugins` so
        `iter_plugins()` finds it.
        """
        plugin_name = getattr(plugin, "name", plugin.__class__.__name__)
        self._plugins[plugin_name] = plugin
        for hook_name, priority in hooks_order.items():
            if hook_name not in ALL_HOOK_NAMES:
                raise ValueError(
                    f"plugin {plugin_name!r} registered against unknown hook {hook_name!r}\n"
                    f"  known hooks: {ALL_HOOK_NAMES}"
                )
            method = getattr(plugin, hook_name, None)
            if method is None or not callable(method):
                raise ValueError(
                    f"plugin {plugin_name!r} declares hook {hook_name!r} in hooks_order "
                    f"but has no callable method by that name"
                )
            self._chains[hook_name].append((priority, plugin_name, method))
            self._chains[hook_name].sort(key=lambda t: t[0])

    def fire(self, hook_name: str, value: Any, **kwargs: Any) -> Any:
        """Run the hook chain. Threads `value` through each plugin.

        Each plugin's method gets called with (**kwargs, value-as-last-positional).
        If a plugin returns None, the value passes through unchanged.
        If a plugin returns a value, that becomes the new value for the next plugin.

        Returns the final value after all plugins (or the original if no plugins).
        """
        chain = self._chains.get(hook_name, [])
        if not chain:
            return value

        current = value
        for _prio, plugin_name, method in chain:
            if plugin_name in self._disabled:
                continue
            try:
                result = method(**kwargs, **{_value_kwarg(hook_name): current})
                if result is not None:
                    current = result
            except (PauseRequested, Cancelled):
                # Control-flow exceptions — propagate, don't count as failures
                raise
            except Exception as exc:
                self._record_failure(plugin_name, hook_name, exc)
                # A gating hook that ERRORS must fail CLOSED — a throwing policy
                # plugin (guard/safety_gate) would otherwise leave the ToolCall
                # untouched and the tool would execute (silent policy bypass).
                if hook_name == "before_tool_call":
                    current = _fail_closed_denial(current, plugin_name, exc)
        return current

    def fire_observer(self, hook_name: str, **kwargs: Any) -> None:
        """Like fire() but for observer hooks that return None.

        Use for: on_session_start/end, on_turn_end, pause_check, on_event.
        No value threading — each plugin runs with the same kwargs.
        """
        chain = self._chains.get(hook_name, [])
        for _prio, plugin_name, method in chain:
            if plugin_name in self._disabled:
                continue
            try:
                method(**kwargs)
            except (PauseRequested, Cancelled):
                raise
            except Exception as exc:
                self._record_failure(plugin_name, hook_name, exc)

    def _record_failure(self, plugin_name: str, hook_name: str, exc: Exception) -> None:
        self._failures[plugin_name] += 1
        # Re-entry guard: if the failing hook IS on_event, emitting a
        # plugin.hook.failed event would fan back out to on_event subscribers
        # and could re-trigger the same failure → infinite recursion. Skip the
        # emit in that case; the failure count still increments so auto-disable
        # still kicks in.
        can_emit = self._bus is not None and hook_name != "on_event"
        if can_emit:
            self._bus.emit(RuntimeEvent(
                type=EventType.PLUGIN_HOOK_FAILED,
                stage="HookRegistry",
                severity=Severity.WARN,
                payload={
                    "plugin": plugin_name,
                    "hook": hook_name,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[: self._exc_msg_max],
                    "failure_count": self._failures[plugin_name],
                },
                content={"traceback": traceback.format_exc()},
            ))
        # Policy plugins that declare `critical = True` (guard, safety_gate) are
        # NEVER auto-quarantined: disabling a gating plugin re-opens the policy
        # bypass. They keep failing closed on before_tool_call instead.
        is_critical = getattr(self._plugins.get(plugin_name), "critical", False)
        if self._failures[plugin_name] >= self._threshold and not is_critical:
            self._disabled.add(plugin_name)
            if can_emit:
                self._bus.emit(RuntimeEvent(
                    type=EventType.PLUGIN_DISABLED,
                    stage="HookRegistry",
                    severity=Severity.WARN,
                    payload={
                        "plugin": plugin_name,
                        "reason": f"exceeded failure threshold ({self._threshold})",
                    },
                ))


# Hook methods that pass their threaded value as a specific kwarg name.
# Most pass it under the name matching the type's purpose. Look up here so
# the registry knows what kwarg to bind the threaded value to.
_HOOK_VALUE_KWARG = {
    "on_turn_start": "user_input",
    "before_llm_call": "req",
    "after_llm_call": "resp",
    "before_tool_call": "call",
    "after_tool_call": "result",
    "pack_context": "messages",
    "assess_step": "result",
}


def _value_kwarg(hook_name: str) -> str:
    return _HOOK_VALUE_KWARG.get(hook_name, "value")


def _fail_closed_denial(value: Any, plugin_name: str, exc: Exception) -> Any:
    """Turn a threaded before_tool_call value into a ToolDenial when a policy
    plugin errored — deny by default. If a prior plugin already denied, keep it."""
    from arc.runtime.hooks import ToolDenial
    if isinstance(value, ToolDenial):
        return value
    return ToolDenial(
        tool_call_id=getattr(value, "tool_call_id", ""),
        name=getattr(value, "name", ""),
        reason=(f"policy plugin {plugin_name!r} errored ({type(exc).__name__}) — "
                f"denying by default (fail-closed)"),
    )


class EventBus:
    """The event bus. Plugins implementing `on_event` subscribe via the registry.

    Core code calls `bus.emit(event)`. The bus delegates to the registry which
    fan-outs to all `on_event` plugins.

    The bus is NOT itself a plugin — it's part of the core. Recorders are
    plugins that listen on it.
    """

    def __init__(self, registry: HookRegistry) -> None:
        self._registry = registry
        registry.bind_bus(self)
        # Stable session context for on_event hooks (set when session starts)
        self._session_ctx: Any = None

    def set_session_context(self, session_ctx: Any) -> None:
        """Called once at session start so on_event plugins know their context."""
        self._session_ctx = session_ctx

    def emit(self, event: RuntimeEvent) -> None:
        """Emit an event to all on_event subscribers.

        Always succeeds — plugin failures are caught by the registry.
        """
        self._registry.fire_observer("on_event", ctx=self._session_ctx, event=event)
