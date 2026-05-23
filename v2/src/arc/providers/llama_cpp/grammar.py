"""JSON-Schema → GBNF compiler for grammar-constrained tool calls.

Used by `llama_cpp`'s grammar mode (see 0015).  llama.cpp's `/completion`
endpoint accepts a `grammar` parameter; if provided, the sampler only
emits tokens whose continuations satisfy the grammar.  We compile the
arc tool list into a grammar that allows exactly one of:

    ANSWER:\\n<free-form text>
    TOOL:\\n{"name": "<tool>", "input": {<args-matching-schema>}}

Field order is canonical (required fields first in `required:` order,
then optional fields in property-declaration order).  The model learns
this order from the postamble; the grammar enforces it.  Out-of-order
emission is *not* a constraint that JSON itself enforces, and the
ergonomic cost is low compared to the value of guaranteed-valid JSON.

Supported JSON-Schema features:
  - object       (with `properties`, `required`)
  - string       (with `enum` of string values)
  - integer
  - number
  - boolean
  - array        (with `items` of any supported type)
  - enum         (any primitive enum)
  - nested object (one level — schemas inside `properties.<x>.properties`)

Explicitly unsupported (raise GrammarCompileError):
  - `pattern` (regex)
  - `anyOf` / `oneOf` / `allOf`
  - `additionalProperties: <schema>` (we allow the unrestricted
    boolean-or-missing form, just don't extend the grammar to match it)
  - `$ref`
  - more than one level of object nesting
"""
from __future__ import annotations

from typing import Any


class GrammarCompileError(ValueError):
    """The tool schema uses a feature the GBNF compiler doesn't support.

    The message names the tool + offending feature so the user knows what
    to remove or simplify.
    """


# ── Primitive rules ────────────────────────────────────────────────────────
#
# These are the same building blocks used by llama.cpp's stock json.gbnf.
# Declared once at the top of every emitted grammar.

_PRIMITIVES = r"""
ws ::= [ \t\n]*
json-string ::= "\"" json-char* "\""
json-char ::= [^"\\\x00-\x1f] | "\\" ["\\/bfnrt] | "\\u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F]
json-integer ::= "-"? ("0" | [1-9] [0-9]*)
json-number ::= "-"? ("0" | [1-9] [0-9]*) ("." [0-9]+)? ([eE] [-+]? [0-9]+)?
json-boolean ::= "true" | "false"
""".strip()


# ── Compiler ───────────────────────────────────────────────────────────────


def compile_grammar(tools: list) -> str:
    """Compile a list of ToolSpec into a GBNF grammar string.

    Returns the full grammar, ready to send as the `grammar` param of
    llama.cpp's `/completion`.

    `tools` items must have `.name` and `.input_schema` (ToolSpec
    duck-type; we don't import ToolSpec here to keep this module
    standalone-testable).
    """
    lines: list[str] = []

    # 1. Root: text answer OR a tool call from the available set.
    if tools:
        tool_names = [_rule_name_for_tool(t.name) for t in tools]
        lines.append("root ::= text-answer | tool-call")
        lines.append('text-answer ::= "ANSWER:\\n" answer-text')
        lines.append("answer-text ::= [^\\x00]+")
        lines.append('tool-call ::= "TOOL:\\n" tool-json')
        lines.append("tool-json ::= " + " | ".join(tool_names))
    else:
        lines.append("root ::= text-answer")
        lines.append('text-answer ::= "ANSWER:\\n" answer-text')
        lines.append("answer-text ::= [^\\x00]+")

    # 2. Per-tool rules.
    for tool in tools:
        rule = _rule_name_for_tool(tool.name)
        # Wrapper: enforce {"name": "<tool>", "input": <schema>}
        input_rule = f"{rule}-input"
        lines.append(
            f'{rule} ::= "{{" ws "\\"name\\":" ws "\\"{tool.name}\\"" ws "," '
            f'ws "\\"input\\":" ws {input_rule} ws "}}"'
        )

        # The input itself is whatever the schema says.
        sub_rules: dict[str, str] = {}
        body = _compile_object_schema(tool.input_schema or {}, rule, sub_rules, depth=0,
                                      tool_name=tool.name)
        lines.append(f"{input_rule} ::= {body}")
        for name, expr in sub_rules.items():
            lines.append(f"{name} ::= {expr}")

    lines.append(_PRIMITIVES)
    return "\n".join(lines) + "\n"


# ── Schema → grammar walker ────────────────────────────────────────────────


_UNSUPPORTED_KEYS = ("pattern", "anyOf", "oneOf", "allOf", "$ref", "patternProperties")


def _check_unsupported(schema: dict, *, tool_name: str, path: str) -> None:
    for key in _UNSUPPORTED_KEYS:
        if key in schema:
            raise GrammarCompileError(
                f"tool {tool_name!r} schema at {path!r} uses {key!r} which the "
                f"grammar compiler doesn't support.  Switch the provider to "
                f"compat mode or simplify the schema."
            )


def _compile_object_schema(
    schema: dict,
    rule_prefix: str,
    sub_rules: dict[str, str],
    *,
    depth: int,
    tool_name: str,
) -> str:
    """Compile a schema-of-type-object into a GBNF expression.

    Field order is: `required` fields first in the order they appear in
    the `required` array, then remaining optional fields in
    `properties`-declaration order.

    Output shapes:
      - No properties at all      → `"{" ws "}"`
      - All-required              → `{ a, b, c }`
      - All-optional              → `{}` | `{ a }` | `{ a, b }` | `{ a, b, c }`
      - Mixed                     → `{ a, [b], [c] }` where [x] is `(",x")?`
    """
    _check_unsupported(schema, tool_name=tool_name, path=rule_prefix)
    if schema.get("type") not in (None, "object"):
        raise GrammarCompileError(
            f"tool {tool_name!r} top-level schema must be type=object, "
            f"got type={schema.get('type')!r}"
        )

    properties: dict[str, Any] = schema.get("properties", {}) or {}
    required: list[str] = list(schema.get("required", []) or [])

    if not properties:
        return '"{" ws "}"'

    # Validate required actually exists in properties; if not, ignore the stray.
    required = [r for r in required if r in properties]

    # Canonical field order: required first (preserving the required list's
    # order), then remaining properties in declaration order.
    ordered: list[str] = list(required)
    for key in properties:
        if key not in ordered:
            ordered.append(key)

    # Compile each property's value rule.
    property_value_rules: dict[str, str] = {}
    for key, subschema in properties.items():
        property_value_rules[key] = _compile_value(
            subschema or {},
            f"{rule_prefix}-{_rule_name_for_property(key)}",
            sub_rules,
            depth=depth + 1,
            tool_name=tool_name,
            path=f"{rule_prefix}.properties.{key}",
        )

    return _emit_object_body(ordered, required, property_value_rules)


def _emit_object_body(
    ordered: list[str],
    required: list[str],
    property_value_rules: dict[str, str],
) -> str:
    """Stitch a `{ ... }` body together honoring required/optional ordering."""
    # Split into required prefix (mandatory) and optional tail.
    required_keys = [k for k in ordered if k in required]
    optional_keys = [k for k in ordered if k not in required]

    if not required_keys:
        # All optional.  Allow empty, or any prefix of the ordered list.
        # Empty: `{}`
        # Non-empty: `{ first ("," next)* }`
        if not optional_keys:
            return '"{" ws "}"'
        first = optional_keys[0]
        parts = [f'"\\"{first}\\":" ws {property_value_rules[first]}']
        for k in optional_keys[1:]:
            parts.append(
                f'(ws "," ws "\\"{k}\\":" ws {property_value_rules[k]})?'
            )
        body = " ".join(parts)
        return f'"{{" ws ({body})? ws "}}"'

    # Mixed or all-required.
    parts: list[str] = []
    for i, k in enumerate(required_keys):
        if i == 0:
            parts.append(f'"\\"{k}\\":" ws {property_value_rules[k]}')
        else:
            parts.append(f'ws "," ws "\\"{k}\\":" ws {property_value_rules[k]}')
    for k in optional_keys:
        parts.append(
            f'(ws "," ws "\\"{k}\\":" ws {property_value_rules[k]})?'
        )
    body = " ".join(parts)
    return f'"{{" ws {body} ws "}}"'


def _compile_value(
    schema: dict,
    rule_prefix: str,
    sub_rules: dict[str, str],
    *,
    depth: int,
    tool_name: str,
    path: str,
) -> str:
    """Compile any value-schema (string/number/.../object) into a GBNF expression
    or a reference to a named sub-rule.
    """
    _check_unsupported(schema, tool_name=tool_name, path=path)

    # Enums are a discriminator that overrides `type` — any primitive enum
    # we accept renders as a fixed `"v1" | "v2" | ...` alternation.
    if "enum" in schema:
        return _compile_enum(schema["enum"], tool_name=tool_name, path=path)

    stype = schema.get("type")

    if stype == "string":
        return "json-string"
    if stype == "integer":
        return "json-integer"
    if stype == "number":
        return "json-number"
    if stype == "boolean":
        return "json-boolean"

    if stype == "array":
        items = schema.get("items") or {}
        item_expr = _compile_value(items, f"{rule_prefix}-item", sub_rules,
                                   depth=depth + 1, tool_name=tool_name,
                                   path=f"{path}.items")
        return f'"[" ws ({item_expr} (ws "," ws {item_expr})*)? ws "]"'

    if stype == "object":
        if depth >= 2:
            raise GrammarCompileError(
                f"tool {tool_name!r} schema at {path!r} nests objects more "
                f"than one level deep, which the grammar compiler doesn't "
                f"support.  Flatten the schema or switch to compat mode."
            )
        # Emit a named sub-rule so the body stays readable.
        sub_name = f"{rule_prefix}-obj"
        sub_rules[sub_name] = _compile_object_schema(
            schema, sub_name, sub_rules, depth=depth + 1, tool_name=tool_name,
        )
        return sub_name

    # Untyped / unknown — fall back to the loosest sane thing: any JSON
    # primitive.  Better than failing on perfectly valid tool schemas that
    # just omit `type:`.
    return "(json-string | json-number | json-integer | json-boolean)"


def _compile_enum(values: list, *, tool_name: str, path: str) -> str:
    if not values:
        raise GrammarCompileError(
            f"tool {tool_name!r} schema at {path!r} has an empty enum"
        )
    alts: list[str] = []
    for v in values:
        if isinstance(v, str):
            alts.append(f'"\\"{_escape_for_gbnf(v)}\\""')
        elif isinstance(v, bool):
            # bool BEFORE int — Python bool isinstance int!  Order matters.
            alts.append('"true"' if v else '"false"')
        elif isinstance(v, int):
            alts.append(f'"{v}"')
        elif isinstance(v, float):
            alts.append(f'"{v}"')
        elif v is None:
            alts.append('"null"')
        else:
            raise GrammarCompileError(
                f"tool {tool_name!r} schema at {path!r} has unsupported "
                f"enum value type: {type(v).__name__}"
            )
    return "(" + " | ".join(alts) + ")"


# ── Naming + escaping ──────────────────────────────────────────────────────


def _rule_name_for_tool(name: str) -> str:
    return "tool-" + _sanitize(name)


def _rule_name_for_property(name: str) -> str:
    return _sanitize(name)


def _sanitize(name: str) -> str:
    """GBNF rule names allow [A-Za-z0-9-_].  Replace anything else with '-'."""
    out_chars: list[str] = []
    for ch in name:
        if ch.isalnum() or ch in "-_":
            out_chars.append(ch)
        else:
            out_chars.append("-")
    out = "".join(out_chars)
    return out.lstrip("-") or "rule"


def _escape_for_gbnf(s: str) -> str:
    """Escape a string literal for embedding inside a GBNF `"..."` token."""
    return (
        s.replace("\\", "\\\\")
         .replace('"', '\\"')
    )
