"""llama.cpp provider — see _design/0015-llama-cpp-provider.md."""
from arc.providers.llama_cpp.provider import DEFAULT_BASE_URL, LlamaCppProvider

__all__ = ["LlamaCppProvider", "DEFAULT_BASE_URL"]
