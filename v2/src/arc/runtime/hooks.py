"""Hook protocols + supporting types.

The hook catalog is defined in _design/0001-foundation-phase0-design.md §4.
Each hook is a Protocol with exactly one method. Plugins implement any subset
of these protocols; the registry composes them at runtime.

Design rules (§4):
  - Return None to mean "no change, pass through unchanged"
  - Return a transformed value to mutate the chain
  - The runtime threads the return value to the next plugin in order
  - Plugins should never have side effects (use on_event or a logger; don't
    mutate global state)

`PASS_THROUGH` sentinel is provided as a synonym for None — improves plugin
code readability ("return PASS_THROUGH" reads clearer than "return None").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

# ── Sentinel for "no change" ────────────────────────────────────────────────
# Plugins can `return PASS_THROUGH` or `return None` — both mean "use the
# pre-hook value as-is". Sentinel is purely a readability aid.

PASS_THROUGH = None


# ── Typed payloads ─────────────────────────────────────────────────────────
# These are the data each hook receives/returns. They're frozen dataclasses
# so plugins can't accidentally mutate shared state — modifications must be
# explicit (return a replaced instance).


@dataclass(frozen=True)
class SessionContext:
    """Stable across the whole session."""
    session_id: str
    workspace: str
    provider_name: str
    provider_model: str
    started_at: str  # ISO timestamp


@dataclass(frozen=True)
class TurnContext:
    """Per-turn context. Includes the session it lives in."""
    session: SessionContext
    turn_id: str
    user_input: str
    iteration: int  # which iteration of the ReAct loop we're in (0-indexed)


@dataclass(frozen=True)
class UserInput:
    """Raw text the user submitted. Plugins can rewrite (e.g., add RAG context)."""
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnOutcome:
    """End-of-turn summary."""
    success: bool
    final_response: str
    n_tool_calls: int
    n_llm_calls: int
    error: str | None = None


@dataclass(frozen=True)
class Message:
    """One conversation message. Provider-agnostic shape."""
    role: str  # "user" | "assistant" | "tool"
    content: Any  # str for user/assistant text; list[ContentBlock] for tool calls/results
    name: str | None = None  # tool name for role=tool


@dataclass(frozen=True)
class ToolSpec:
    """A tool's schema as the provider expects it."""
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema


@dataclass(frozen=True)
class LLMRequest:
    """Everything the runtime is about to send to the provider.

    Plugins can swap models, augment system prompts, filter tools, etc.
    The provider receives whatever the final hook returns.
    """
    messages: list[Message]
    system: str
    tools: list[ToolSpec]
    model: str
    params: dict[str, Any]  # temperature, max_tokens, etc.


@dataclass(frozen=True)
class ContentBlock:
    """One block inside an LLM response. Either text or a tool call.

    `metadata` is a free-form dict for provider-specific fields the runtime
    doesn't interpret but must echo back (e.g., Gemini's thought_signature
    on function_call parts — required by Gemini 3+ for follow-up turns).
    """
    type: str  # "text" | "tool_use"
    text: str | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class LLMResponse:
    """What the provider returned. Plugins can validate, retry, transform."""
    content: list[ContentBlock]
    stop_reason: str  # "end_turn" | "tool_use" | "max_tokens" | other
    input_tokens: int
    output_tokens: int
    raw: dict[str, Any] = field(default_factory=dict)  # full provider response for byte-fidelity


@dataclass(frozen=True)
class ToolCall:
    """A tool invocation about to happen."""
    tool_call_id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """What a tool returned. ok=False signals failure to the caller."""
    tool_call_id: str
    name: str
    ok: bool
    output: str  # canonical string the model will see
    error_code: str | None = None


@dataclass(frozen=True)
class ToolDenial:
    """Special return from before_tool_call short-circuiting execution.
    The runtime feeds `reason` to the model as if it were the tool's output.
    """
    tool_call_id: str
    name: str
    reason: str


# ── Hook protocols ─────────────────────────────────────────────────────────
# Twelve hooks per design §4. Each is a Protocol with exactly one method.
# Plugins implement any subset. Method signatures are versioned in the docstring
# (e.g., "v1") — we'll add _v2 variants when we evolve them, per §5.5.


class OnSessionStart(Protocol):
    """v1. Fires once at session boot. Observe only — return value ignored."""
    def on_session_start(self, ctx: SessionContext) -> None: ...


class OnSessionEnd(Protocol):
    """v1. Fires once at session exit. Observe only."""
    def on_session_end(self, ctx: SessionContext, outcome: TurnOutcome | None) -> None: ...


class OnTurnStart(Protocol):
    """v1. Fires when a user turn begins. Plugin can rewrite the user input."""
    def on_turn_start(self, ctx: TurnContext, user_input: UserInput) -> UserInput | None: ...


class OnTurnEnd(Protocol):
    """v1. Fires when a turn finishes."""
    def on_turn_end(self, ctx: TurnContext, outcome: TurnOutcome) -> None: ...


class BeforeLLMCall(Protocol):
    """v1. Fires before each provider call. Plugin can modify the full request."""
    def before_llm_call(self, ctx: TurnContext, req: LLMRequest) -> LLMRequest | None: ...


class AfterLLMCall(Protocol):
    """v1. Fires after each provider call. Plugin can modify the response."""
    def after_llm_call(self, ctx: TurnContext, req: LLMRequest, resp: LLMResponse) -> LLMResponse | None: ...


class BeforeToolCall(Protocol):
    """v1. Fires before each tool execution. Return ToolDenial to short-circuit."""
    def before_tool_call(self, ctx: TurnContext, call: ToolCall) -> ToolCall | ToolDenial | None: ...


class AfterToolCall(Protocol):
    """v1. Fires after each tool execution. Plugin can modify the result."""
    def after_tool_call(self, ctx: TurnContext, call: ToolCall, result: ToolResult) -> ToolResult | None: ...


class PackContext(Protocol):
    """v1. Fires when building messages for the next LLM call.
    Plugin returns a possibly-filtered/reordered/compressed message list.
    """
    def pack_context(self, ctx: TurnContext, messages: list[Message], query: str) -> list[Message] | None: ...


@dataclass(frozen=True)
class Step:
    """A 'step' in the agent's work. Defined by step-aware plugins (planner)."""
    index: int
    description: str
    tool: str | None = None


@dataclass(frozen=True)
class StepAssessment:
    """Plugin's verdict on a step."""
    decision: str  # "continue" | "replan" | "retry" | "stop" | "goal_achieved"
    reason: str = ""
    confidence: float = 1.0


class AssessStep(Protocol):
    """v1. Fires after each step boundary (only when a plugin defines steps)."""
    def assess_step(self, ctx: TurnContext, step: Step, result: str) -> StepAssessment | None: ...


# Forward import to avoid circular dep — RuntimeEvent is defined in events.py.
# Plugins receive it by type at hook invocation time.
from arc.runtime.events import RuntimeEvent  # noqa: E402


class OnEvent(Protocol):
    """v1. Fires for every event emitted on the bus. Observe only.
    Recorders, persisters, and external monitors implement this.
    """
    def on_event(self, ctx: SessionContext, event: RuntimeEvent) -> None: ...


class PauseRequested(Exception):
    """Raise from pause_check to checkpoint the agent. Runtime catches and pauses."""


class Cancelled(Exception):
    """Raise from pause_check to abort the current turn. Runtime catches and ends."""


class PauseCheck(Protocol):
    """v1. Fires at cooperative yield points. Raise to pause/cancel."""
    def pause_check(self, ctx: TurnContext) -> None: ...


# ── Hook name catalog ─────────────────────────────────────────────────────
# Single source of truth for hook names. Used by the registry to introspect
# plugins and dispatch correctly.

ALL_HOOK_NAMES = (
    "on_session_start",
    "on_session_end",
    "on_turn_start",
    "on_turn_end",
    "before_llm_call",
    "after_llm_call",
    "before_tool_call",
    "after_tool_call",
    "pack_context",
    "assess_step",
    "on_event",
    "pause_check",
)
