from typing import Optional
from functools import lru_cache
from pydantic import AliasChoices, AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def env_alias(*names: str) -> AliasChoices:
    return AliasChoices(*names)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=True, extra="ignore", populate_by_name=True
    )

    env: str = Field(default="dev", validation_alias=env_alias("ENVIRONMENT", "env"))

    # === API KEYS ===
    anthropic_api_key: Optional[str] = Field(
        default=None,
        validation_alias=env_alias("ANTHROPIC_API_KEY", "anthropic_api_key"),
    )

    anthropic_model: str = Field(
        default="claude-3-5-haiku-latest",
        validation_alias=env_alias("ANTHROPIC_MODEL", "anthropic_model"),
    )

    openai_api_key: Optional[str] = Field(
        default=None,
        validation_alias=env_alias("OPENAI_API_KEY", "openai_api_key"),
    )

    openai_model: str = Field(
        default="gpt-4o-mini",
        validation_alias=env_alias("OPENAI_MODEL", "openai_model"),
    )

    # === LLM PROVIDER ===
    llm_provider: str = Field(
        default="anthropic",
        validation_alias=env_alias("LLM_PROVIDER", "llm_provider"),
    )

    ollama_base_url: str = Field(
        default="http://localhost:11434/v1",
        validation_alias=env_alias("OLLAMA_BASE_URL", "ollama_base_url"),
    )

    ollama_model: str = Field(
        default="llama3.2",
        validation_alias=env_alias("OLLAMA_MODEL", "ollama_model"),
    )

    # === GROK (xAI) ===
    grok_api_key: Optional[str] = Field(
        default=None,
        validation_alias=env_alias("GROK_API_KEY", "grok_api_key"),
    )

    grok_model: str = Field(
        default="grok-3-mini",
        validation_alias=env_alias("GROK_MODEL", "grok_model"),
    )

    # === DEEPSEEK ===
    deepseek_api_key: Optional[str] = Field(
        default=None,
        validation_alias=env_alias("DEEPSEEK_API_KEY", "deepseek_api_key"),
    )

    deepseek_model: str = Field(
        default="deepseek-chat",
        validation_alias=env_alias("DEEPSEEK_MODEL", "deepseek_model"),
    )

    # === GEMINI ===
    gemini_api_key: Optional[str] = Field(
        default=None,
        validation_alias=env_alias("GEMINI_API_KEY", "gemini_api_key"),
    )

    gemini_model: str = Field(
        default="gemini-2.0-flash",
        validation_alias=env_alias("GEMINI_MODEL", "gemini_model"),
    )

    # === BRAVE SEARCH ===
    brave_api_key: Optional[str] = Field(
        default=None,
        validation_alias=env_alias("BRAVE_API_KEY", "brave_api_key"),
    )

    # === DATABASE ===
    agent_db_url: str = Field(
        default="sqlite+aiosqlite:///./data/agent.db",
        validation_alias=env_alias("AGENT_DB_URL", "agent_db_url"),
    )

    briefbot_db_path: Optional[str] = Field(
        default=None,
        validation_alias=env_alias("BRIEFBOT_DB_PATH", "briefbot_db_path"),
    )

    enable_session_persistence: bool = Field(
        default=False,
        validation_alias=env_alias("ENABLE_SESSION_PERSISTENCE", "enable_session_persistence"),
    )

    # === RUNTIME LAYER ===
    runtime_provider: Optional[str] = Field(
        default=None,
        validation_alias=env_alias("RUNTIME_PROVIDER", "runtime_provider"),
    )

    runtime_model: Optional[str] = Field(
        default=None,
        validation_alias=env_alias("RUNTIME_MODEL", "runtime_model"),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
