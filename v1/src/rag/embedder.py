"""Embedding abstraction — provider-swappable via config.

Add new providers by subclassing Embedder and registering in get_embedder().
"""
from __future__ import annotations
from abc import ABC, abstractmethod


class Embedder(ABC):
    @property
    @abstractmethod
    def dim(self) -> int: ...

    @abstractmethod
    def embed(self, text: str) -> list[float]: ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class SentenceTransformerEmbedder(Embedder):
    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_name)
        self._dim: int = self._model.get_sentence_embedding_dimension()

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        emb = self._model.encode(text[:4000], show_progress_bar=False)
        if hasattr(emb, "tolist"):
            emb = emb.tolist()
        return [float(x) for x in emb]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        truncated = [t[:4000] for t in texts]
        embs = self._model.encode(truncated, show_progress_bar=False)
        return [[float(x) for x in e] for e in embs]


class OpenAIEmbedder(Embedder):
    # text-embedding-3-small → 1536 dims
    _DIMS = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072, "text-embedding-ada-002": 1536}

    def __init__(self, model_name: str = "text-embedding-3-small") -> None:
        import openai
        self._client = openai.OpenAI()
        self._model = model_name
        self._dim = self._DIMS.get(model_name, 1536)

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        resp = self._client.embeddings.create(input=text[:8000], model=self._model)
        return resp.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(input=[t[:8000] for t in texts], model=self._model)
        return [item.embedding for item in resp.data]


class GeminiEmbedder(Embedder):
    # text-embedding-004 → 768 dims
    def __init__(self, model_name: str = "models/text-embedding-004") -> None:
        self._model = model_name
        self._dim = 768

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        import google.generativeai as genai
        result = genai.embed_content(model=self._model, content=text[:8000])
        return result["embedding"]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


def get_embedder(provider: str, model: str) -> Embedder:
    if provider == "sentence_transformers":
        return SentenceTransformerEmbedder(model)
    if provider == "openai":
        return OpenAIEmbedder(model)
    if provider == "gemini":
        return GeminiEmbedder(model)
    raise ValueError(f"unknown embedding provider: {provider!r}")
