"""Unit tests for the JSON-Schema → GBNF compiler."""
from __future__ import annotations

import re

import pytest

from arc.providers.llama_cpp.grammar import GrammarCompileError, compile_grammar
from arc.runtime.hooks import ToolSpec


def _tool(name, schema):
    return ToolSpec(name=name, description="t", input_schema=schema)


# ── Top-level shape ────────────────────────────────────────────────────────


def test_empty_tool_list_emits_text_answer_only():
    grammar = compile_grammar([])
    assert "root ::= text-answer" in grammar
    # No tool-call alt when no tools
    assert "| tool-call" not in grammar
    assert "tool-call ::=" not in grammar


def test_root_has_text_or_tool_alternative_when_tools_present():
    grammar = compile_grammar([
        _tool("ls", {"type": "object", "properties": {}, "required": []}),
    ])
    assert "root ::= text-answer | tool-call" in grammar
    assert "tool-call ::= \"TOOL:\\n\" tool-json" in grammar
    assert "tool-json ::= tool-ls" in grammar


def test_multiple_tools_listed_in_alt():
    grammar = compile_grammar([
        _tool("ls", {"type": "object", "properties": {}}),
        _tool("bash_exec", {"type": "object", "properties": {}}),
    ])
    assert "tool-json ::= tool-ls | tool-bash_exec" in grammar


def test_text_answer_rule_uses_freeform_content():
    grammar = compile_grammar([])
    # Allows any non-null content past the ANSWER:\n prefix
    assert 'text-answer ::= "ANSWER:\\n" answer-text' in grammar
    assert "answer-text ::= [^\\x00]+" in grammar


def test_primitives_block_is_appended():
    grammar = compile_grammar([])
    # Sanity: the json-string / json-integer / etc. rules are present
    for rule in ("ws ::=", "json-string ::=", "json-integer ::=",
                 "json-number ::=", "json-boolean ::="):
        assert rule in grammar


# ── Per-tool input shapes ──────────────────────────────────────────────────


def test_object_with_string_property_required():
    grammar = compile_grammar([
        _tool("ls", {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }),
    ])
    assert 'tool-ls-input ::= "{" ws "\\"path\\":" ws json-string ws "}"' in grammar


def test_object_with_required_and_optional_fields():
    grammar = compile_grammar([
        _tool("ls", {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_depth": {"type": "integer"},
            },
            "required": ["path"],
        }),
    ])
    # required path first, then optional max_depth
    assert (
        'tool-ls-input ::= "{" ws "\\"path\\":" ws json-string '
        '(ws "," ws "\\"max_depth\\":" ws json-integer)? ws "}"'
    ) in grammar


def test_object_with_only_optional_fields_allows_empty_object():
    grammar = compile_grammar([
        _tool("ls", {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": [],
        }),
    ])
    # The body is wrapped in (...)? so {} is a valid match
    assert "tool-ls-input ::=" in grammar
    line = next(l for l in grammar.splitlines() if l.startswith("tool-ls-input ::="))
    assert "{" in line and "}" in line
    assert "?" in line  # optional wrapper


def test_object_with_no_properties_emits_empty_braces():
    grammar = compile_grammar([
        _tool("noop", {"type": "object", "properties": {}}),
    ])
    assert 'tool-noop-input ::= "{" ws "}"' in grammar


# ── Property types ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("schema_type,rule", [
    ("string", "json-string"),
    ("integer", "json-integer"),
    ("number", "json-number"),
    ("boolean", "json-boolean"),
])
def test_primitive_property_types_map_to_named_rules(schema_type, rule):
    grammar = compile_grammar([
        _tool("t", {
            "type": "object",
            "properties": {"x": {"type": schema_type}},
            "required": ["x"],
        }),
    ])
    assert f'"\\"x\\":" ws {rule}' in grammar


def test_array_of_strings():
    grammar = compile_grammar([
        _tool("t", {
            "type": "object",
            "properties": {
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["tags"],
        }),
    ])
    assert '"[" ws (json-string (ws "," ws json-string)*)? ws "]"' in grammar


def test_string_enum():
    grammar = compile_grammar([
        _tool("t", {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["fast", "slow"]},
            },
            "required": ["mode"],
        }),
    ])
    assert '("\\"fast\\"" | "\\"slow\\"")' in grammar


def test_integer_enum():
    grammar = compile_grammar([
        _tool("t", {
            "type": "object",
            "properties": {"level": {"type": "integer", "enum": [1, 2, 3]}},
            "required": ["level"],
        }),
    ])
    assert '("1" | "2" | "3")' in grammar


def test_nested_object_one_level():
    grammar = compile_grammar([
        _tool("t", {
            "type": "object",
            "properties": {
                "opts": {
                    "type": "object",
                    "properties": {"verbose": {"type": "boolean"}},
                    "required": ["verbose"],
                },
            },
            "required": ["opts"],
        }),
    ])
    # Sub-rule was emitted
    assert re.search(r"^tool-t-opts-obj ::=", grammar, re.MULTILINE) is not None
    # And referenced from the parent
    assert '"\\"opts\\":" ws tool-t-opts-obj' in grammar


def test_untyped_property_falls_back_to_any_primitive():
    grammar = compile_grammar([
        _tool("t", {
            "type": "object",
            "properties": {"x": {}},
            "required": ["x"],
        }),
    ])
    assert "(json-string | json-number | json-integer | json-boolean)" in grammar


# ── Unsupported features ───────────────────────────────────────────────────


def test_regex_pattern_raises():
    with pytest.raises(GrammarCompileError, match="pattern"):
        compile_grammar([
            _tool("t", {
                "type": "object",
                "properties": {"id": {"type": "string", "pattern": r"^[A-Z]+$"}},
                "required": ["id"],
            }),
        ])


def test_anyof_raises():
    with pytest.raises(GrammarCompileError, match="anyOf"):
        compile_grammar([
            _tool("t", {
                "type": "object",
                "properties": {
                    "v": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                },
                "required": ["v"],
            }),
        ])


def test_double_nested_object_raises():
    with pytest.raises(GrammarCompileError, match="more than one level"):
        compile_grammar([
            _tool("t", {
                "type": "object",
                "properties": {
                    "outer": {
                        "type": "object",
                        "properties": {
                            "inner": {
                                "type": "object",
                                "properties": {"x": {"type": "string"}},
                            },
                        },
                    },
                },
            }),
        ])


def test_empty_enum_raises():
    with pytest.raises(GrammarCompileError, match="empty enum"):
        compile_grammar([
            _tool("t", {
                "type": "object",
                "properties": {"x": {"type": "string", "enum": []}},
                "required": ["x"],
            }),
        ])


def test_non_object_top_level_raises():
    with pytest.raises(GrammarCompileError, match="must be type=object"):
        compile_grammar([_tool("t", {"type": "string"})])


# ── Grammar shape sanity ───────────────────────────────────────────────────


def test_tool_name_with_hyphens_sanitized():
    grammar = compile_grammar([
        _tool("web-search", {"type": "object", "properties": {}}),
    ])
    # Rule names use the cleaned name, but the string literal preserves the original
    assert "tool-web-search ::=" in grammar
    assert '"\\"name\\":" ws "\\"web-search\\""' in grammar


def test_emitted_grammar_is_nonempty_string():
    grammar = compile_grammar([
        _tool("ls", {"type": "object", "properties": {"path": {"type": "string"}},
                     "required": ["path"]}),
    ])
    assert isinstance(grammar, str)
    assert grammar.endswith("\n")
    assert len(grammar) > 100  # sanity floor
