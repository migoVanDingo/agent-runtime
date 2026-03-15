# Project 6: RAG — Retrieval-Augmented Generation

## Prerequisites
Projects 1–5. You should have a traced, provider-agnostic, persistent agent.

## What You Will Build

A retrieval pipeline that gives your agent access to a large codebase or document set without stuffing everything into the context window. The agent embeds documents, stores them in a vector index, and retrieves only the relevant chunks when answering a question.

This is the **semantic memory upgrade** for your agent — replacing the flat `SemanticStore` from Project 3 with a searchable embedding index.

## Concepts

### Why RAG?

Your context window is finite. A real codebase has hundreds of files. If you naively include all of them in every prompt:
- You hit the context limit
- Costs balloon (paying for tokens the model never needs)
- Signal gets diluted by irrelevant content

RAG solves this by answering: *which parts of the corpus are relevant to this specific query?*

```
Query: "how does authentication work?"
        │
        ▼
  Embed query → [0.12, -0.34, 0.87, ...]
        │
        ▼
  Vector search → top-k similar chunks
        │
        ▼
  Inject only those chunks into context
        │
        ▼
  Model answers with grounded context
```

### Chunks, Not Files

Files are too large and too heterogeneous to embed as units. Split them into **chunks**:
- Code: one function or class per chunk
- Prose: ~300 token paragraphs with 50-token overlap

```
file: auth.py
  → chunk: "def login(user, password): ..."
  → chunk: "def logout(session_id): ..."
  → chunk: "def validate_token(token): ..."
```

Each chunk gets its own embedding vector. Retrieval operates at the chunk level.

### Embeddings

An embedding is a dense vector that encodes semantic meaning. Similar texts have similar vectors (high cosine similarity).

```python
embed("login with password")  → [0.12, -0.34, 0.87, ...]
embed("authenticate user")    → [0.11, -0.32, 0.89, ...]  # similar!
embed("parse CSV file")       → [-0.55, 0.23, -0.41, ...]  # different
```

We'll use Anthropic's `voyage-code-2` or OpenAI's `text-embedding-3-small` — both are cheap and fast.

### Vector Store (Simple)

For this project, we use a simple in-memory vector store with cosine similarity search. No external dependencies. For production, you'd swap in ChromaDB, FAISS, or Pinecone — but the interface stays the same.

```
┌─────────────────────────┐
│     VectorStore         │
│                         │
│  chunks: list[Chunk]    │
│  vectors: np.ndarray    │
│                         │
│  add(chunk, vector)     │
│  search(query_vec, k=5) │
│  save() / load()        │
└─────────────────────────┘
```

## Architecture

```
Files / Docs
    │
    │ chunk + embed
    ▼
┌──────────────────┐
│   Indexer        │  ← one-time build step
│  (offline)       │
└────────┬─────────┘
         │ writes
         ▼
┌──────────────────┐
│  VectorStore     │  ← .agent/rag/index.pkl
└────────┬─────────┘
         │ queried by
         ▼
┌──────────────────┐
│  Retriever       │  ← called at runtime
│  .retrieve(q, k) │
└────────┬─────────┘
         │ returns top-k chunks
         ▼
┌──────────────────┐
│  Agent           │  ← injects chunks into context
│  retrieve_code   │  ← tool the model can call
└──────────────────┘
```

## Build Guide

### Step 1: Define the data structures

Create `rag/types.py`:

```python
from dataclasses import dataclass, field

@dataclass
class Chunk:
    chunk_id: str
    source: str          # file path
    content: str         # the actual text
    chunk_type: str      # "function" | "class" | "paragraph" | "file"
    metadata: dict = field(default_factory=dict)
    # e.g. {"function_name": "login", "line_start": 42, "line_end": 58}
```

### Step 2: Chunker

Create `rag/chunker.py`:

```python
import ast
import re
from pathlib import Path
from .types import Chunk
import uuid

def chunk_python_file(path: str) -> list[Chunk]:
    """Split a Python file into function/class chunks using AST."""
    source = Path(path).read_text()
    chunks = []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Fall back to whole-file chunk
        return [Chunk(
            chunk_id=str(uuid.uuid4())[:8],
            source=path,
            content=source[:4000],
            chunk_type="file"
        )]

    lines = source.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # Only top-level and first-level class methods
            start = node.lineno - 1
            end = node.end_lineno
            content = "\n".join(lines[start:end])

            chunk_type = "class" if isinstance(node, ast.ClassDef) else "function"
            chunks.append(Chunk(
                chunk_id=str(uuid.uuid4())[:8],
                source=path,
                content=content,
                chunk_type=chunk_type,
                metadata={
                    "name": node.name,
                    "line_start": node.lineno,
                    "line_end": node.end_lineno,
                }
            ))

    return chunks if chunks else [Chunk(
        chunk_id=str(uuid.uuid4())[:8],
        source=path,
        content=source[:4000],
        chunk_type="file"
    )]


def chunk_text_file(path: str, chunk_size: int = 400, overlap: int = 50) -> list[Chunk]:
    """Split a text/markdown file into overlapping paragraph chunks."""
    content = Path(path).read_text()
    words = content.split()
    chunks = []
    i = 0

    while i < len(words):
        chunk_words = words[i:i + chunk_size]
        chunk_content = " ".join(chunk_words)
        chunks.append(Chunk(
            chunk_id=str(uuid.uuid4())[:8],
            source=path,
            content=chunk_content,
            chunk_type="paragraph",
            metadata={"word_start": i}
        ))
        i += chunk_size - overlap

    return chunks


def chunk_file(path: str) -> list[Chunk]:
    """Dispatch to the right chunker based on file extension."""
    ext = Path(path).suffix.lower()
    if ext == ".py":
        return chunk_python_file(path)
    elif ext in (".md", ".txt", ".rst"):
        return chunk_text_file(path)
    else:
        # Generic: treat as text
        return chunk_text_file(path)
```

### Step 3: Embedding

Create `rag/embedder.py`. We'll support two backends:

```python
import os
from typing import Protocol

class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...

class OpenAIEmbedder:
    """Uses text-embedding-3-small — cheap and fast."""
    def __init__(self, model: str = "text-embedding-3-small"):
        import openai
        self.client = openai.OpenAI()
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(
            input=texts,
            model=self.model
        )
        return [item.embedding for item in response.data]


class VoyageEmbedder:
    """Uses voyage-code-2 — optimized for code."""
    def __init__(self, model: str = "voyage-code-2"):
        import voyageai
        self.client = voyageai.Client()
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = self.client.embed(texts, model=self.model, input_type="document")
        return result.embeddings


def create_embedder(backend: str = "openai") -> Embedder:
    if backend == "openai":
        return OpenAIEmbedder()
    elif backend == "voyage":
        return VoyageEmbedder()
    else:
        raise ValueError(f"Unknown embedder: {backend}")
```

### Step 4: Vector store

Create `rag/vector_store.py`:

```python
import json
import pickle
import numpy as np
from pathlib import Path
from .types import Chunk


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


class VectorStore:
    def __init__(self, store_path: str = ".agent/rag/index.pkl"):
        self.store_path = Path(store_path)
        self.chunks: list[Chunk] = []
        self.vectors: list[list[float]] = []

    def add(self, chunk: Chunk, vector: list[float]):
        self.chunks.append(chunk)
        self.vectors.append(vector)

    def search(self, query_vector: list[float], k: int = 5) -> list[tuple[Chunk, float]]:
        if not self.vectors:
            return []

        q = np.array(query_vector)
        scores = [cosine_similarity(q, np.array(v)) for v in self.vectors]
        top_indices = np.argsort(scores)[::-1][:k]

        return [(self.chunks[i], scores[i]) for i in top_indices]

    def save(self):
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.store_path, "wb") as f:
            pickle.dump({"chunks": self.chunks, "vectors": self.vectors}, f)

    def load(self) -> bool:
        if not self.store_path.exists():
            return False
        with open(self.store_path, "rb") as f:
            data = pickle.load(f)
        self.chunks = data["chunks"]
        self.vectors = data["vectors"]
        return True

    @property
    def size(self) -> int:
        return len(self.chunks)
```

### Step 5: Indexer (build the index)

Create `rag/indexer.py`:

```python
import glob
from pathlib import Path
from .chunker import chunk_file
from .embedder import create_embedder
from .vector_store import VectorStore


def build_index(
    paths: list[str],
    embedder_backend: str = "openai",
    store_path: str = ".agent/rag/index.pkl",
    batch_size: int = 32,
) -> VectorStore:
    """
    Index a list of files or glob patterns.

    Example:
        build_index(["src/**/*.py", "docs/**/*.md"])
    """
    embedder = create_embedder(embedder_backend)
    store = VectorStore(store_path)

    # Expand globs
    file_paths = []
    for pattern in paths:
        matched = glob.glob(pattern, recursive=True)
        file_paths.extend(matched)

    print(f"Indexing {len(file_paths)} files...")

    # Chunk all files
    all_chunks = []
    for path in file_paths:
        try:
            chunks = chunk_file(path)
            all_chunks.extend(chunks)
        except Exception as e:
            print(f"  Skip {path}: {e}")

    print(f"  {len(all_chunks)} chunks total")

    # Embed in batches
    for i in range(0, len(all_chunks), batch_size):
        batch = all_chunks[i:i + batch_size]
        texts = [c.content for c in batch]
        vectors = embedder.embed(texts)

        for chunk, vector in zip(batch, vectors):
            store.add(chunk, vector)

        print(f"  Embedded {min(i + batch_size, len(all_chunks))}/{len(all_chunks)}")

    store.save()
    print(f"Index saved: {store.size} vectors at {store_path}")
    return store
```

### Step 6: Retriever

Create `rag/retriever.py`:

```python
from .embedder import create_embedder
from .vector_store import VectorStore
from .types import Chunk


class Retriever:
    def __init__(
        self,
        store_path: str = ".agent/rag/index.pkl",
        embedder_backend: str = "openai",
    ):
        self.store = VectorStore(store_path)
        self.store.load()
        self.embedder = create_embedder(embedder_backend)

    def retrieve(self, query: str, k: int = 5) -> list[tuple[Chunk, float]]:
        """Return the top-k most relevant chunks for a query."""
        vectors = self.embedder.embed([query])
        return self.store.search(vectors[0], k=k)

    def retrieve_as_context(self, query: str, k: int = 5) -> str:
        """Format top-k chunks as a context string for injection into prompts."""
        results = self.retrieve(query, k=k)
        if not results:
            return ""

        parts = []
        for chunk, score in results:
            header = f"# {chunk.source}"
            if chunk.metadata.get("name"):
                header += f" — {chunk.chunk_type} `{chunk.metadata['name']}`"
            header += f" (relevance: {score:.2f})"
            parts.append(f"{header}\n```\n{chunk.content}\n```")

        return "\n\n".join(parts)
```

### Step 7: Give the agent a retrieval tool

Add this tool to your agent from Project 2/3:

```python
# Tool schema
RETRIEVE_TOOL = {
    "name": "retrieve_code",
    "description": (
        "Search the codebase for code or documentation relevant to a query. "
        "Returns the most semantically similar code chunks. "
        "Use this before reading individual files — it's faster and more targeted."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What you're looking for, in plain English"
            },
            "k": {
                "type": "integer",
                "description": "Number of results to return (default 5)",
                "default": 5
            }
        },
        "required": ["query"]
    }
}

# Tool implementation
from rag.retriever import Retriever

retriever = Retriever()

def execute_retrieve_code(query: str, k: int = 5) -> str:
    return retriever.retrieve_as_context(query, k=k)
```

### Step 8: CLI indexer script

Create `index_codebase.py` so you can build the index from the command line:

```python
#!/usr/bin/env python3
"""
Usage:
    python index_codebase.py src/**/*.py docs/**/*.md
    python index_codebase.py .  # index everything
"""
import sys
from rag.indexer import build_index

if __name__ == "__main__":
    patterns = sys.argv[1:] if len(sys.argv) > 1 else ["**/*.py"]
    build_index(patterns)
```

## Success Criteria

- [ ] `python index_codebase.py src/**/*.py` builds an index without errors
- [ ] `retriever.retrieve("how does authentication work")` returns relevant chunks
- [ ] Agent uses `retrieve_code` tool before reading individual files
- [ ] Retrieval is meaningfully better than random (spot check 5 queries)
- [ ] Index persists to disk and loads on restart
- [ ] Adding a new file and re-indexing updates the results

## Notes on Embedding APIs

| Provider | Model | Cost (per 1M tokens) | Good for |
|----------|-------|---------------------|---------|
| OpenAI | text-embedding-3-small | $0.02 | General text, docs |
| OpenAI | text-embedding-3-large | $0.13 | Higher accuracy |
| Voyage | voyage-code-2 | $0.12 | Code (better than OpenAI for code) |
| Ollama | nomic-embed-text | Free (local) | Offline, privacy |

For local embeddings with Ollama:
```bash
ollama pull nomic-embed-text
```

```python
class OllamaEmbedder:
    def __init__(self, model: str = "nomic-embed-text"):
        self.model = model
        self.base_url = "http://localhost:11434"

    def embed(self, texts: list[str]) -> list[list[float]]:
        import requests
        vectors = []
        for text in texts:
            resp = requests.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": text}
            ).json()
            vectors.append(resp["embedding"])
        return vectors
```

## What's Missing

| Gap | Fixed in |
|-----|---------|
| Vector store is in-memory / pickle | Swap in ChromaDB or FAISS for production |
| No re-ranking after retrieval | Add cross-encoder re-ranker for better precision |
| Chunking is naive (no semantic boundaries) | Tree-sitter for language-aware code splitting |
| Index goes stale when files change | Add file-watcher to auto-reindex on save |
| No hybrid search (keyword + semantic) | BM25 + embedding fusion |
