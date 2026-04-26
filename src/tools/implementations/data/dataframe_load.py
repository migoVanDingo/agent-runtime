"""dataframe_load - load a file or artifact into a named dataframe artifact."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from logger import get_logger
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight

logger = get_logger(__name__)

_SUPPORTED_FORMATS = ("csv", "tsv", "json", "jsonl", "parquet", "html")


class DataframeLoadTool(BaseTool):
    name = "dataframe_load"
    description = (
        "Load CSV/TSV/JSON/JSONL/Parquet/HTML data into a named dataframe artifact. "
        "Source can be a file path or an artifact key."
    )
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "source": ToolProperty(
                    type="string",
                    description="File path or artifact key",
                ),
                "name": ToolProperty(
                    type="string",
                    description="Artifact key to store dataframe under",
                ),
                "format": ToolProperty(
                    type="string",
                    description=(
                        "Optional format override: "
                        + ", ".join(_SUPPORTED_FORMATS)
                        + ". Auto-detected when omitted."
                    ),
                ),
            },
            required=["source", "name"],
        )

    def execute(self, tool_input: dict) -> str:
        try:
            import pandas as pd
        except Exception:
            return "Error: pandas is not installed."

        source = str(tool_input["source"])
        name = str(tool_input["name"])
        fmt = str(tool_input.get("format", "")).strip().lower()

        store = self._store()
        artifact_value: Any | None = None
        artifact_kind: str | None = None
        path_source: Path | None = None

        p = Path(source)
        if p.exists() and p.is_file():
            path_source = p
        elif store is not None:
            m = store.meta(source)
            if m is not None:
                artifact_kind = m.kind
                artifact_value = store.get(source)
                if artifact_value is None and m.data_path:
                    data_path = Path(m.data_path)
                    if data_path.exists():
                        path_source = data_path

        if isinstance(artifact_value, pd.DataFrame):
            df = artifact_value.copy()
        else:
            resolved_fmt = fmt or self._detect_format(path_source, source, artifact_kind, artifact_value)
            if resolved_fmt not in _SUPPORTED_FORMATS:
                return (
                    "Error: unsupported format '"
                    + resolved_fmt
                    + "'. Supported: "
                    + ", ".join(_SUPPORTED_FORMATS)
                )

            try:
                df = self._load_dataframe(
                    path_source=path_source,
                    raw_value=artifact_value,
                    fmt=resolved_fmt,
                    source_label=source,
                )
            except Exception as e:
                return f"Error: failed to load source '{source}' as {resolved_fmt}: {e}"

        if df is None or df.empty and len(df.columns) == 0:
            return f"Error: no dataframe content could be loaded from '{source}'."

        if store is None:
            return "Error: artifact store is not initialized."

        try:
            store.set(name, df, kind="dataframe", source=source)
            meta = store.meta(name)
        except Exception as e:
            return f"Error: failed to store dataframe artifact '{name}': {e}"

        summary = meta.summary if meta else _df_summary(df)
        return f"Loaded dataframe artifact '{name}'\n{summary}"

    def _load_dataframe(
        self,
        path_source: Path | None,
        raw_value: Any | None,
        fmt: str,
        source_label: str,
    ):
        import pandas as pd

        if fmt == "parquet":
            if path_source is None:
                raise ValueError("parquet requires a file-backed source")
            return pd.read_parquet(str(path_source))

        if path_source is not None:
            return self._load_from_path(path_source, fmt)

        if raw_value is None:
            raise ValueError("source was not found as a file path or artifact")

        if isinstance(raw_value, (dict, list)):
            if fmt in ("json", "jsonl"):
                return pd.json_normalize(raw_value) if isinstance(raw_value, dict) else pd.DataFrame(raw_value)
            raise ValueError(f"artifact value is JSON-like but format is '{fmt}'")

        text = str(raw_value)
        return self._load_from_text(text, fmt, source_label)

    def _load_from_path(self, path: Path, fmt: str):
        import pandas as pd

        if fmt == "csv":
            return pd.read_csv(str(path))
        if fmt == "tsv":
            return pd.read_csv(str(path), sep="\t")
        if fmt == "json":
            return _read_json_any(path.read_text(encoding="utf-8"))
        if fmt == "jsonl":
            return pd.read_json(str(path), lines=True)
        if fmt == "parquet":
            return pd.read_parquet(str(path))
        if fmt == "html":
            tables = pd.read_html(str(path))
            if not tables:
                raise ValueError("no tables found")
            return tables[0]
        raise ValueError(f"unsupported format '{fmt}'")

    def _load_from_text(self, text: str, fmt: str, source_label: str):
        import pandas as pd

        if fmt == "csv":
            return pd.read_csv(io.StringIO(text))
        if fmt == "tsv":
            return pd.read_csv(io.StringIO(text), sep="\t")
        if fmt == "json":
            return _read_json_any(text)
        if fmt == "jsonl":
            return pd.read_json(io.StringIO(text), lines=True)
        if fmt == "html":
            tables = pd.read_html(io.StringIO(text))
            if not tables:
                raise ValueError(f"no HTML tables found in '{source_label}'")
            return tables[0]
        raise ValueError(f"format '{fmt}' requires a file-backed source")

    def _detect_format(
        self,
        path_source: Path | None,
        source: str,
        artifact_kind: str | None,
        artifact_value: Any | None,
    ) -> str:
        if path_source is not None:
            ext = path_source.suffix.lower().lstrip(".")
        else:
            ext = Path(source).suffix.lower().lstrip(".")

        mapping = {
            "csv": "csv",
            "tsv": "tsv",
            "json": "json",
            "jsonl": "jsonl",
            "parquet": "parquet",
            "html": "html",
            "htm": "html",
        }
        if ext in mapping:
            return mapping[ext]

        if artifact_kind == "dataframe":
            return "csv"

        if isinstance(artifact_value, str):
            stripped = artifact_value.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                return "json"

        if isinstance(artifact_value, (dict, list)):
            return "json"

        return "csv"

    def _store(self):
        try:
            from runtime.artifact_store import get_artifact_store

            return get_artifact_store()
        except Exception:
            return None


def _read_json_any(text: str):
    import pandas as pd

    parsed = json.loads(text)
    if isinstance(parsed, list):
        return pd.DataFrame(parsed)
    if isinstance(parsed, dict):
        return pd.json_normalize(parsed)
    return pd.DataFrame({"value": [parsed]})


def _df_summary(df) -> str:
    cols = ", ".join(f"{c} ({df[c].dtype})" for c in df.columns)
    sample = df.head(3).to_string(index=False)
    return f"shape={df.shape}  columns=[{cols}]\n{sample}"
