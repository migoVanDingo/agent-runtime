"""regex_match - regex find/extract/replace across file, artifact, or raw text."""

from __future__ import annotations

import json
import re
from pathlib import Path

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight


class RegexMatchTool(BaseTool):
    name = "regex_match"
    description = (
        "Run regex find/extract/replace against a file path, artifact key, or raw text. "
        "Find mode includes line numbers and local context."
    )
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "source": ToolProperty(
                    type="string",
                    description="File path, artifact key, or raw text",
                ),
                "pattern": ToolProperty(
                    type="string",
                    description="Regex pattern",
                ),
                "mode": ToolProperty(
                    type="string",
                    description="find (default), extract, or replace",
                ),
                "flags": ToolProperty(
                    type="string",
                    description="Regex flags as letters: i, m, s",
                ),
                "replacement": ToolProperty(
                    type="string",
                    description="Replacement string used only when mode='replace'",
                ),
                "output": ToolProperty(
                    type="string",
                    description="Optional artifact key to store result text",
                ),
            },
            required=["source", "pattern"],
        )

    def execute(self, tool_input: dict) -> str:
        source = str(tool_input["source"])
        pattern = str(tool_input["pattern"])
        mode = str(tool_input.get("mode", "find")).strip().lower() or "find"
        flags_text = str(tool_input.get("flags", ""))
        replacement = str(tool_input.get("replacement", ""))
        output = tool_input.get("output")

        if mode not in ("find", "extract", "replace"):
            return "Error: mode must be one of: find, extract, replace"

        text = self._resolve_text(source)
        if text is None:
            return f"Error: source '{source}' is neither an existing file path nor an artifact key."

        try:
            regex = re.compile(pattern, _parse_flags(flags_text))
        except re.error as e:
            return f"Error: invalid regex pattern: {e}"

        if mode == "replace":
            rendered = regex.sub(replacement, text)
            msg = (
                f"Applied replacement ({len(text)} -> {len(rendered)} chars).\n"
                f"Preview:\n{rendered[:1000]}"
            )
            self._store_output(output, rendered)
            return msg

        matches = list(regex.finditer(text))
        if not matches:
            return "No regex matches found."

        max_matches = 200
        if mode == "extract":
            extracted = []
            for m in matches[:max_matches]:
                if m.groups():
                    extracted.append(m.group(1) if len(m.groups()) == 1 else m.groups())
                else:
                    extracted.append(m.group(0))
            rendered = "\n".join(_to_line(v) for v in extracted)
            if len(matches) > max_matches:
                rendered += f"\n[truncated: showing first {max_matches} of {len(matches)} matches]"
            self._store_output(output, rendered)
            return rendered

        # mode == "find" with context
        lines = text.splitlines()
        blocks = []
        for idx, m in enumerate(matches[:max_matches], start=1):
            line_no = text.count("\n", 0, m.start()) + 1
            start_line = max(1, line_no - 2)
            end_line = min(len(lines), line_no + 2)
            blocks.append(f"[{idx}] line {line_no}: {m.group(0)}")
            for ln in range(start_line, end_line + 1):
                marker = ">" if ln == line_no else " "
                content = lines[ln - 1] if ln - 1 < len(lines) else ""
                blocks.append(f"{marker}{ln:>6} | {content}")
            blocks.append("")

        rendered = "\n".join(blocks).rstrip()
        if len(matches) > max_matches:
            rendered += f"\n\n[truncated: showing first {max_matches} of {len(matches)} matches]"

        self._store_output(output, rendered)
        return rendered

    def _resolve_text(self, source: str) -> str | None:
        p = Path(source)
        if p.exists() and p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")

        store = self._store()
        if store is not None:
            m = store.meta(source)
            if m is not None:
                value = store.get(source)
                if value is None:
                    return None
                return _to_text(value)

        # Treat as raw text input.
        return source

    def _store_output(self, output_key: str | None, value: str) -> None:
        if not output_key:
            return
        store = self._store()
        if store is None:
            return
        store.set(str(output_key), value, kind="string", source="regex_match")

    def _store(self):
        try:
            from runtime.artifact_store import get_artifact_store

            return get_artifact_store()
        except Exception:
            return None


def _parse_flags(flags_text: str) -> int:
    f = 0
    if "i" in flags_text:
        f |= re.IGNORECASE
    if "m" in flags_text:
        f |= re.MULTILINE
    if "s" in flags_text:
        f |= re.DOTALL
    return f


def _to_text(value) -> str:
    try:
        import pandas as pd

        if isinstance(value, pd.DataFrame):
            return value.to_csv(index=False)
        if isinstance(value, pd.Series):
            return value.to_csv(index=True)
    except Exception:
        pass

    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False)
    return str(value)


def _to_line(value) -> str:
    if isinstance(value, tuple):
        return " | ".join(str(v) for v in value)
    return str(value)
