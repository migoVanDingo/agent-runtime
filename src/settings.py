"""Runtime settings.

.env contains ONLY secrets (API keys, DB URLs, feature flags).
Provider selection and model names come from config.yml [llm] section.
Settings fields that are no longer env-driven keep their defaults for
backwards compatibility but config.yml takes precedence via get_provider().
"""
from typing import Optional
from functools import lru_cache
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def env_alias(*names: str) -> AliasChoices:
    return AliasChoices(*names)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=True, extra="ignore", populate_by_name=True
    )

    env: str = Field(default="dev", validation_alias=env_alias("ENVIRONMENT", "env"))

    # ── API Keys (secrets — live in .env, never in config.yml) ────────────────

    anthropic_api_key: Optional[str] = Field(
        default=None, validation_alias=env_alias("ANTHROPIC_API_KEY"),
    )
    openai_api_key: Optional[str] = Field(
        default=None, validation_alias=env_alias("OPENAI_API_KEY"),
    )
    grok_api_key: Optional[str] = Field(
        default=None, validation_alias=env_alias("GROK_API_KEY"),
    )
    deepseek_api_key: Optional[str] = Field(
        default=None, validation_alias=env_alias("DEEPSEEK_API_KEY"),
    )
    gemini_api_key: Optional[str] = Field(
        default=None, validation_alias=env_alias("GEMINI_API_KEY"),
    )
    brave_api_key: Optional[str] = Field(
        default=None, validation_alias=env_alias("BRAVE_API_KEY"),
    )

    # ── Ollama (URL is infrastructure, not a key, so .env is fine) ───────────
    ollama_base_url: str = Field(
        default="http://localhost:11434/v1",
        validation_alias=env_alias("OLLAMA_BASE_URL"),
    )

    # ── Database / persistence (infrastructure secrets) ───────────────────────
    agent_db_url: str = Field(
        default="sqlite+aiosqlite:///./data/agent.db",
        validation_alias=env_alias("AGENT_DB_URL"),
    )
    briefbot_db_path: Optional[str] = Field(
        default=None, validation_alias=env_alias("BRIEFBOT_DB_PATH"),
    )
    enable_session_persistence: bool = Field(
        default=False, validation_alias=env_alias("ENABLE_SESSION_PERSISTENCE"),
    )

    # ── Tool paths (machine-local, vary per install — live in .env) ──────────
    ghidra_home: Optional[str] = Field(
        default=None, validation_alias=env_alias("GHIDRA_HOME"),
    )

    # ── Deprecated env overrides (kept for backwards compat, config.yml wins) ─
    # These are read by get_provider() only if config.yml doesn't specify a model.
    # Prefer setting llm.provider / llm.model in config.yml instead.
    llm_provider: Optional[str] = Field(
        default=None, validation_alias=env_alias("LLM_PROVIDER"),
    )
    anthropic_model: Optional[str] = Field(
        default=None, validation_alias=env_alias("ANTHROPIC_MODEL"),
    )
    openai_model: Optional[str] = Field(
        default=None, validation_alias=env_alias("OPENAI_MODEL"),
    )
    ollama_model: Optional[str] = Field(
        default=None, validation_alias=env_alias("OLLAMA_MODEL"),
    )
    grok_model: Optional[str] = Field(
        default=None, validation_alias=env_alias("GROK_MODEL"),
    )
    deepseek_model: Optional[str] = Field(
        default=None, validation_alias=env_alias("DEEPSEEK_MODEL"),
    )
    gemini_model: Optional[str] = Field(
        default=None, validation_alias=env_alias("GEMINI_MODEL"),
    )
    runtime_provider: Optional[str] = Field(
        default=None, validation_alias=env_alias("RUNTIME_PROVIDER"),
    )
    runtime_model: Optional[str] = Field(
        default=None, validation_alias=env_alias("RUNTIME_MODEL"),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
