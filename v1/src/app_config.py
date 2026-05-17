from functools import lru_cache
from config import AppConfig, load_config
from settings import Settings


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return load_config()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


config = get_config()
settings = get_settings()
