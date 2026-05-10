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
        # _analysis/<binary_name>/file.txt → parts index -2 is the binary dir
        try:
            idx = parts.index("_analysis")
            if idx + 1 < len(parts):
                binary_name = parts[idx + 1]
        except ValueError:
            pass

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
