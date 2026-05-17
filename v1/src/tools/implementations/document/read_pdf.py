"""read_pdf — extract text from a PDF file."""
from pathlib import Path
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from logger import get_logger

logger = get_logger(__name__)

_INLINE_CAP = 40_000
_ARTIFACT_PREFIX = "pdf"


def _parse_page_range(spec: str, total: int) -> list[int]:
    """Parse a page spec like '1', '1-5', '3,7,12' into 0-indexed page numbers."""
    pages = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, _, end = part.partition("-")
            try:
                s = max(1, int(start.strip()))
                e = min(total, int(end.strip()))
                pages.update(range(s - 1, e))
            except ValueError:
                pass
        else:
            try:
                p = int(part)
                if 1 <= p <= total:
                    pages.add(p - 1)
            except ValueError:
                pass
    return sorted(pages)


class ReadPdfTool(BaseTool):
    name = "read_pdf"
    description = (
        "Extract text from a PDF file. "
        "Specify pages as '1', '1-5', or '3,7,12' (1-indexed). "
        "Set artifact_key to store the full text as a named artifact for later reading."
    )
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(
                    type="string",
                    description="Path to the PDF file",
                ),
                "pages": ToolProperty(
                    type="string",
                    description="Pages to extract: '1', '1-5', '3,7,12'. Default: all pages.",
                ),
                "artifact_key": ToolProperty(
                    type="string",
                    description="Store extracted text under this artifact key for later reading",
                ),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        try:
            import pypdf
        except ImportError:
            return "Error: pypdf is not installed. Run: pip install pypdf"

        path = Path(tool_input["path"])
        if not path.exists():
            return f"Error: file not found: {path}"
        if not path.is_file():
            return f"Error: not a file: {path}"

        pages_spec = tool_input.get("pages", "")
        artifact_key = tool_input.get("artifact_key", "")

        try:
            reader = pypdf.PdfReader(str(path))
        except pypdf.errors.PdfReadError as e:
            return f"Error: {path.name} does not appear to be a valid PDF: {e}"
        except Exception as e:
            return f"Error: failed to open PDF: {e}"

        if reader.is_encrypted:
            return f"Error: PDF is encrypted and requires a password: {path}"

        total_pages = len(reader.pages)
        if pages_spec:
            page_indices = _parse_page_range(pages_spec, total_pages)
            if not page_indices:
                return f"Error: page spec '{pages_spec}' produced no valid pages (file has {total_pages} pages)"
        else:
            page_indices = list(range(total_pages))

        parts = []
        for idx in page_indices:
            try:
                text = reader.pages[idx].extract_text() or ""
                parts.append(f"[Page {idx + 1}]\n{text.strip()}")
            except Exception as e:
                parts.append(f"[Page {idx + 1}]\n(extraction failed: {e})")

        full_text = "\n\n".join(parts)

        # Store as artifact if requested
        if artifact_key:
            try:
                from runtime.artifact_store import get_artifact_store
                store = get_artifact_store()
                store.set(artifact_key.strip(), full_text, kind="url_content", source=str(path))
                stored_path = store.meta(artifact_key.strip())
                path_hint = f" → {stored_path.data_path}" if stored_path and stored_path.data_path else ""
                logger.info(f"  read_pdf: stored {len(full_text)} chars as artifact '{artifact_key}'{path_hint}")
            except Exception as e:
                logger.warning(f"  read_pdf: artifact store unavailable: {e}")

        header = (
            f"PDF: {path}\n"
            f"Pages: {total_pages} total"
        )
        if pages_spec:
            header += f"  (extracting: {pages_spec})"
        header += "\n"

        if len(full_text) > _INLINE_CAP:
            truncated = full_text[:_INLINE_CAP]
            note = (
                f"\n[output truncated at {_INLINE_CAP:,} chars — "
                f"{len(full_text):,} chars total. "
            )
            if artifact_key:
                note += f"Use get_artifact '{artifact_key}' or read_file_lines to read the rest.]"
            else:
                note += f"Re-run with artifact_key='pdf_content' to store the full text.]"
            return header + "\n" + truncated + note
        else:
            return header + "\n" + full_text
