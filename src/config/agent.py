"""Agent configuration dataclass."""
from dataclasses import dataclass


@dataclass
class AgentConfig:
    system_prompt: str
