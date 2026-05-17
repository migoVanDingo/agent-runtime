from __future__ import annotations
from pathlib import Path
from rag.schema import Chunk


def chunk_text(
    text: str,
    source_file: str = "",
    size: int = 1500,
    overlap: int = 300,
) -> list[Chunk]:
    """Split text into overlapping fixed-size chunks with source metadata."""
    if not text:
        return []

    binary_name = ""
    if source_file:
        parts = Path(source_file).parts
        # Look for either the new on-disk layout (<ARC_HOME>/analysis/<binary>/...)
        # or the agent-facing logical path (_analysis/<binary>/...).
        for marker in ("analysis", "_analysis"):
            try:
                idx = parts.index(marker)
                if idx + 1 < len(parts):
                    binary_name = parts[idx + 1]
                break
            except ValueError:
                continue

    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(Chunk(
            text=text[start:end],
            source_file=source_file,
            offset=start,
            binary_name=binary_name,
        ))
        if end == len(text):
            break
        start += size - overlap
    return chunks
