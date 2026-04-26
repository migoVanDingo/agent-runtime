"""read_epub — extract text from an EPUB e-book."""
from pathlib import Path
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from logger import get_logger

logger = get_logger(__name__)

_INLINE_CAP = 40_000


def _parse_chapter_spec(spec: str, total: int) -> list[int]:
    """Parse '1', '1-5', '2,4,6' into 0-indexed chapter indices."""
    indices = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, _, end = part.partition("-")
            try:
                s = max(1, int(start.strip()))
                e = min(total, int(end.strip()))
                indices.update(range(s - 1, e))
            except ValueError:
                pass
        else:
            try:
                i = int(part)
                if 1 <= i <= total:
                    indices.add(i - 1)
            except ValueError:
                pass
    return sorted(indices)


class ReadEpubTool(BaseTool):
    name = "read_epub"
    description = (
        "Extract text from an EPUB e-book. "
        "Optionally restrict to specific chapters by index (1-indexed). "
        "Set artifact_key to store the text as a named artifact."
    )
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(
                    type="string",
                    description="Path to the .epub file",
                ),
                "chapters": ToolProperty(
                    type="string",
                    description="Chapters to extract: '1', '1-5', '2,4,6'. Default: all.",
                ),
                "artifact_key": ToolProperty(
                    type="string",
                    description="Store extracted text as a named artifact",
                ),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        try:
            import ebooklib
            from ebooklib import epub
            from bs4 import BeautifulSoup
        except ImportError as e:
            missing = str(e).split("'")[-2] if "'" in str(e) else str(e)
            return (
                f"Error: required package not installed: {missing}. "
                f"Run: pip install ebooklib beautifulsoup4"
            )

        path = Path(tool_input["path"])
        if not path.exists():
            return f"Error: file not found: {path}"
        if not path.is_file():
            return f"Error: not a file: {path}"

        chapters_spec = (tool_input.get("chapters") or "").strip()
        artifact_key = (tool_input.get("artifact_key") or "").strip()

        try:
            book = epub.read_epub(str(path))
        except Exception as e:
            return f"Error: could not open EPUB: {e}"

        # Collect document items in spine order
        items = [
            item for item in book.get_items()
            if item.get_type() == ebooklib.ITEM_DOCUMENT
        ]

        if not items:
            return f"Error: no readable content found in {path.name}"

        total = len(items)
        if chapters_spec:
            chapter_indices = _parse_chapter_spec(chapters_spec, total)
            if not chapter_indices:
                return f"Error: chapter spec '{chapters_spec}' produced no valid indices (book has {total} chapters)"
            selected = [items[i] for i in chapter_indices]
        else:
            selected = items

        parts = []
        for i, item in enumerate(selected):
            try:
                soup = BeautifulSoup(item.get_body_content(), "html.parser")
                text = soup.get_text(separator="\n").strip()
                if text:
                    name = item.get_name() or f"Chapter {i + 1}"
                    parts.append(f"[{name}]\n{text}")
            except Exception as e:
                parts.append(f"[Chapter {i + 1}] (extraction failed: {e})")

        full_text = "\n\n".join(parts)

        if artifact_key:
            try:
                from runtime.artifact_store import get_artifact_store
                store = get_artifact_store()
                store.set(artifact_key, full_text, kind="url_content", source=str(path))
                logger.info(f"  read_epub: stored {len(full_text)} chars as artifact '{artifact_key}'")
            except Exception as e:
                logger.warning(f"  read_epub: artifact store unavailable: {e}")

        title = book.title or path.stem
        header = (
            f"EPUB: {path}\n"
            f"Title: {title}\n"
            f"Chapters: {total} total"
        )
        if chapters_spec:
            header += f"  (extracting: {chapters_spec})"
        header += "\n"

        if len(full_text) > _INLINE_CAP:
            truncated = full_text[:_INLINE_CAP]
            note = f"\n[truncated at {_INLINE_CAP:,} chars — {len(full_text):,} total. "
            if artifact_key:
                note += f"Use get_artifact '{artifact_key}' to read the rest.]"
            else:
                note += f"Re-run with artifact_key='epub_content' to store the full text.]"
            return header + "\n" + truncated + note
        else:
            return header + "\n" + full_text
