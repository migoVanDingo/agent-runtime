"""RAG (Retrieval-Augmented Generation) configuration dataclass."""
from dataclasses import dataclass


@dataclass
class RagConfig:
    enabled: bool = False
    mode: str = "local"                              # local | http
    http_base_url: str = "http://localhost:17433"   # used when mode=http
    embedding_provider: str = "sentence_transformers"
    embedding_model: str = "all-MiniLM-L6-v2"
    top_k: int = 5
    threshold: float = 0.65
    injection_budget_chars: int = 2000
