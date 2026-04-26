"""template_render - render Jinja2 templates with provided variables."""

from __future__ import annotations

from pathlib import Path

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight


class TemplateRenderTool(BaseTool):
    name = "template_render"
    description = "Render a Jinja2 template string or file path with provided variables."
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "template": ToolProperty(
                    type="string",
                    description="Template string or template file path",
                ),
                "variables": ToolProperty(
                    type="object",
                    description="Variables object passed into the template",
                ),
                "output": ToolProperty(
                    type="string",
                    description="Optional output file path for rendered content",
                ),
            },
            required=["template", "variables"],
        )

    def execute(self, tool_input: dict) -> str:
        try:
            from jinja2 import Environment, StrictUndefined
        except Exception:
            return "Error: jinja2 is not installed."

        template_input = str(tool_input["template"])
        variables = tool_input.get("variables", {})
        output = tool_input.get("output")

        if not isinstance(variables, dict):
            return "Error: 'variables' must be an object."

        template_str = self._resolve_template(template_input)

        env = Environment(undefined=StrictUndefined, autoescape=False)
        try:
            tmpl = env.from_string(template_str)
            rendered = tmpl.render(**variables)
        except Exception as e:
            return f"Error: template render failed: {e}"

        if output:
            out_path = Path(str(output))
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(rendered, encoding="utf-8")
            return f"Rendered template to {out_path} ({len(rendered)} chars)."

        return rendered

    def _resolve_template(self, template_input: str) -> str:
        p = Path(template_input)
        if p.exists() and p.is_file():
            return p.read_text(encoding="utf-8")
        return template_input
