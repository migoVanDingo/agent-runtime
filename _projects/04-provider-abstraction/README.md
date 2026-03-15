# Project 4: Provider Abstraction

## Prerequisites
Projects 1–3. You should have a working persistent agent.

## What You Will Build

A unified `LLMProvider` interface that lets you swap Anthropic, OpenAI, and local Ollama models without changing your agent code. Every project from here onward uses this interface instead of the Anthropic SDK directly.

This implements Cruz's **Model-Agnostic Operation** principle.

## Concepts

### Why Abstraction Now?

You have 3+ more projects to build. Every one of them will call a model. If they all import `anthropic` directly:
- Switching to OpenAI requires editing 8 files
- Testing with a local model requires editing 8 files
- A future provider (Gemini, Mistral, local fine-tune) requires editing 8 files

The abstraction costs one hour to build and saves hours every subsequent project.

### The Interface

Every provider needs to do two things: complete a message and stream a message.

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class Message:
    role: str          # "user" | "assistant" | "system"
    content: str | list

@dataclass
class ToolCall:
    id: str
    name: str
    input: dict

@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall]
    stop_reason: str   # "end_turn" | "tool_use" | "max_tokens"
    input_tokens: int
    output_tokens: int

class LLMProvider(ABC):
    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> LLMResponse:
        ...

    @abstractmethod
    def model_id(self) -> str:
        ...
```

### Tool Schema Normalization

Anthropic and OpenAI use slightly different tool schema formats. The abstraction layer normalizes to one format (we'll use Anthropic's, since it's what you already know), and each provider adapter translates:

```python
# Our canonical tool format (Anthropic-style):
{
    "name": "read_file",
    "description": "Read the contents of a file",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"]
    }
}

# OpenAI format (the adapter converts):
{
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read the contents of a file",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"]
        }
    }
}
```

## Architecture

```
Your Agent Code
      │
      │ calls
      ↓
┌─────────────────┐
│   LLMProvider   │  ← abstract interface
│  .complete()    │
└────────┬────────┘
         │ implemented by
    ┌────┴─────────────────┐
    ↓         ↓            ↓
Anthropic   OpenAI      Ollama
Provider    Provider    Provider
    │         │            │
    ↓         ↓            ↓
anthropic   openai      requests
  SDK        SDK         HTTP
```

## Build Guide

### Step 1: Define the base types

Create `providers/base.py`:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

@dataclass
class ToolCall:
    id: str
    name: str
    input: dict

@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

class LLMProvider(ABC):
    @abstractmethod
    def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        ...

    @abstractmethod
    def model_id(self) -> str:
        ...
```

### Step 2: Anthropic provider

Create `providers/anthropic_provider.py`:

```python
import anthropic as sdk
from .base import LLMProvider, LLMResponse, ToolCall

class AnthropicProvider(LLMProvider):
    def __init__(self, model: str = "claude-opus-4-6", api_key: str | None = None):
        self.model = model
        self.client = sdk.Anthropic(api_key=api_key)

    def model_id(self) -> str:
        return self.model

    def complete(self, messages, tools=None, system=None, max_tokens=4096) -> LLMResponse:
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        response = self.client.messages.create(**kwargs)

        text = None
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                text = block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    input=block.input
                ))

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
```

### Step 3: OpenAI provider

Create `providers/openai_provider.py`:

```python
import openai as sdk
import json
from .base import LLMProvider, LLMResponse, ToolCall

def _to_openai_tool(tool: dict) -> dict:
    """Convert Anthropic-style tool schema to OpenAI format."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
        }
    }

def _to_openai_messages(messages: list[dict]) -> list[dict]:
    """Convert message history (may contain Anthropic tool_result blocks) to OpenAI format."""
    result = []
    for msg in messages:
        if isinstance(msg["content"], str):
            result.append({"role": msg["role"], "content": msg["content"]})
        elif isinstance(msg["content"], list):
            for block in msg["content"]:
                if isinstance(block, dict):
                    if block.get("type") == "tool_result":
                        result.append({
                            "role": "tool",
                            "tool_call_id": block["tool_use_id"],
                            "content": block["content"]
                        })
                    elif block.get("type") == "tool_use":
                        # assistant tool call — handled via finish_reason
                        pass
    return result

class OpenAIProvider(LLMProvider):
    def __init__(self, model: str = "gpt-4o", api_key: str | None = None):
        self.model = model
        self.client = sdk.OpenAI(api_key=api_key)

    def model_id(self) -> str:
        return self.model

    def complete(self, messages, tools=None, system=None, max_tokens=4096) -> LLMResponse:
        oai_messages = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        oai_messages.extend(_to_openai_messages(messages))

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": oai_messages,
        }
        if tools:
            kwargs["tools"] = [_to_openai_tool(t) for t in tools]

        response = self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        text = choice.message.content
        tool_calls = []

        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    input=json.loads(tc.function.arguments)
                ))

        stop_reason = "end_turn"
        if choice.finish_reason == "tool_calls":
            stop_reason = "tool_use"

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
        )
```

### Step 4: Ollama provider (local models)

Create `providers/ollama_provider.py`. Ollama runs locally and exposes an OpenAI-compatible API:

```python
import requests
import json
from .base import LLMProvider, LLMResponse, ToolCall

class OllamaProvider(LLMProvider):
    """
    Requires Ollama running locally: https://ollama.ai
    Install: brew install ollama
    Pull a model: ollama pull llama3.2 or ollama pull qwen2.5-coder
    Run: ollama serve
    """
    def __init__(self, model: str = "llama3.2", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url

    def model_id(self) -> str:
        return f"ollama/{self.model}"

    def complete(self, messages, tools=None, system=None, max_tokens=4096) -> LLMResponse:
        oai_messages = []
        if system:
            oai_messages.append({"role": "system", "content": system})

        for msg in messages:
            if isinstance(msg["content"], str):
                oai_messages.append({"role": msg["role"], "content": msg["content"]})
            # Note: Ollama tool support varies by model — handle gracefully

        payload = {
            "model": self.model,
            "messages": oai_messages,
            "stream": False,
        }
        if tools:
            # Ollama supports tools for some models (llama3.1+, qwen2.5-coder)
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {}),
                    }
                }
                for t in tools
            ]

        response = requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=120
        ).json()

        message = response.get("message", {})
        text = message.get("content")
        tool_calls = []

        if message.get("tool_calls"):
            for tc in message["tool_calls"]:
                fn = tc.get("function", {})
                tool_calls.append(ToolCall(
                    id=str(id(tc)),  # Ollama doesn't always give IDs
                    name=fn.get("name", ""),
                    input=fn.get("arguments", {})
                ))

        stop_reason = "tool_use" if tool_calls else "end_turn"

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
        )
```

### Step 5: Provider factory

Create `providers/__init__.py` with a factory function:

```python
from .base import LLMProvider, LLMResponse, ToolCall
from .anthropic_provider import AnthropicProvider
from .openai_provider import OpenAIProvider
from .ollama_provider import OllamaProvider

def create_provider(provider: str = "anthropic", model: str | None = None) -> LLMProvider:
    """
    Create a provider by name.

    Examples:
        create_provider("anthropic")
        create_provider("anthropic", model="claude-haiku-4-5-20251001")
        create_provider("openai", model="gpt-4o-mini")
        create_provider("ollama", model="llama3.2")
    """
    defaults = {
        "anthropic": "claude-opus-4-6",
        "openai": "gpt-4o",
        "ollama": "llama3.2",
    }
    model = model or defaults.get(provider, "")

    if provider == "anthropic":
        return AnthropicProvider(model=model)
    elif provider == "openai":
        return OpenAIProvider(model=model)
    elif provider == "ollama":
        return OllamaProvider(model=model)
    else:
        raise ValueError(f"Unknown provider: {provider}")

__all__ = ["LLMProvider", "LLMResponse", "ToolCall", "create_provider"]
```

### Step 6: Update your agent to use the abstraction

Change your agent to accept a provider:

```python
from providers import create_provider, LLMProvider

class Agent:
    def __init__(self, provider: LLMProvider, tools: list, system: str):
        self.provider = provider
        self.tools = tools
        self.system = system

    def run_turn(self, messages: list) -> str:
        while True:
            response = self.provider.complete(
                messages=messages,
                tools=self.tools,
                system=self.system,
            )
            # ... same loop as before ...
```

Now you can do:
```python
agent = Agent(provider=create_provider("anthropic"), ...)
agent = Agent(provider=create_provider("ollama", "llama3.2"), ...)
agent = Agent(provider=create_provider("openai", "gpt-4o-mini"), ...)
```

## Success Criteria

- [ ] Same agent code runs with Anthropic, OpenAI, and Ollama providers
- [ ] Switching providers requires changing only one line
- [ ] Tool calls work with Anthropic provider (verified)
- [ ] Tool calls work with OpenAI provider (verified)
- [ ] Ollama works for simple text tasks (tool support depends on model)
- [ ] Token counts are tracked in `LLMResponse` for all providers

## Notes on Local Models

Not all local models support tool calling. Good options that do:
- `qwen2.5-coder:7b` — excellent at code tasks, supports tools
- `llama3.1:8b` — good general purpose, supports tools
- `mistral:7b` — decent, partial tool support

For models without tool support, you can implement a fallback: parse tool calls from the model's text output using a simple format. This is covered as an exercise.
