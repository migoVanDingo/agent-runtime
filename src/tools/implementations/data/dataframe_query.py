"""dataframe_query - evaluate a pandas expression against stored dataframes."""

from __future__ import annotations

import json
from typing import Any

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight


class DataframeQueryTool(BaseTool):
    name = "dataframe_query"
    description = (
        "Run a pandas expression using named dataframe artifacts. "
        "Provide 'dataframes' as alias->artifact key mapping."
    )
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "expression": ToolProperty(
                    type="string",
                    description="Python pandas expression to evaluate, e.g. df[df['score'] > 90]",
                ),
                "dataframes": ToolProperty(
                    type="object",
                    description="Object mapping alias -> dataframe artifact key, e.g. {'df': 'sales_data'}",
                ),
                "output": ToolProperty(
                    type="string",
                    description="Optional artifact key to store the query result",
                ),
                "format": ToolProperty(
                    type="string",
                    description="Return format: table (default), csv, or json",
                ),
            },
            required=["expression", "dataframes"],
        )

    def execute(self, tool_input: dict) -> str:
        try:
            import pandas as pd
        except Exception:
            return "Error: pandas is not installed."

        expression = str(tool_input["expression"])
        dataframes = tool_input["dataframes"]
        output = tool_input.get("output")
        out_fmt = str(tool_input.get("format", "table")).strip().lower() or "table"

        if not isinstance(dataframes, dict) or not dataframes:
            return "Error: 'dataframes' must be a non-empty object mapping alias->artifact key."

        store = self._store()
        if store is None:
            return "Error: artifact store is not initialized."

        context: dict[str, Any] = {"pd": pd}
        for alias, key in dataframes.items():
            if not isinstance(alias, str):
                return "Error: dataframe aliases must be strings."
            value = store.get(str(key))
            if value is None:
                return f"Error: dataframe artifact '{key}' was not found."
            if not isinstance(value, pd.DataFrame):
                return f"Error: artifact '{key}' is not a dataframe."
            context[alias] = value

        try:
            result = eval(expression, {"__builtins__": {}}, context)
        except Exception as e:
            return f"Error: dataframe_query expression failed: {e}"

        rendered = _render_result(result, out_fmt)
        if rendered.startswith("Error:"):
            return rendered

        store_line = ""
        if output:
            try:
                kind, store_value = _value_for_storage(result)
                store.set(str(output), store_value, kind=kind, source=f"dataframe_query:{expression[:120]}")
                store_line = f"Stored result as artifact '{output}'.\n"
            except Exception as e:
                return f"Error: query succeeded but storing output failed: {e}"

        return f"{store_line}{rendered}"

    def _store(self):
        try:
            from runtime.artifact_store import get_artifact_store

            return get_artifact_store()
        except Exception:
            return None


def _render_result(value: Any, out_fmt: str) -> str:
    import pandas as pd

    if out_fmt not in ("table", "csv", "json"):
        return "Error: format must be one of: table, csv, json"

    if isinstance(value, pd.DataFrame):
        if out_fmt == "csv":
            return value.to_csv(index=False)
        if out_fmt == "json":
            return value.to_json(orient="records", indent=2)
        max_rows = 200
        clipped = value.head(max_rows)
        note = ""
        if len(value) > max_rows:
            note = f"\n[truncated: showing first {max_rows} of {len(value)} rows]"
        return clipped.to_string(index=False) + note

    if isinstance(value, pd.Series):
        if out_fmt == "csv":
            return value.to_csv(index=True)
        if out_fmt == "json":
            return value.to_json(indent=2)
        max_rows = 200
        clipped = value.head(max_rows)
        note = ""
        if len(value) > max_rows:
            note = f"\n[truncated: showing first {max_rows} of {len(value)} rows]"
        return clipped.to_string() + note

    if out_fmt == "json":
        try:
            return json.dumps(value, indent=2, ensure_ascii=False, default=str)
        except Exception:
            return str(value)

    return str(value)


def _value_for_storage(value: Any) -> tuple[str, Any]:
    import pandas as pd

    if isinstance(value, pd.DataFrame):
        return "dataframe", value
    if isinstance(value, pd.Series):
        name = value.name or "value"
        return "dataframe", value.to_frame(name=name)
    if isinstance(value, str):
        return "string", value
    return "result", value
