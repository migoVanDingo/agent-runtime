"""Unit tests for `arc setup`'s catalog.yml loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from arc.defaults import DEFAULT_CATALOG_YAML
from arc.setup.catalog import (
    MANUAL_ENTRY_ID,
    CatalogEntry,
    CatalogError,
    append_manual_sentinel,
    load_catalog,
)


# ── Default catalog ────────────────────────────────────────────────────────


def test_default_catalog_yaml_parses_without_error(tmp_path: Path):
    path = tmp_path / "catalog.yml"
    path.write_text(DEFAULT_CATALOG_YAML)
    cat = load_catalog(path)
    assert "anthropic" in cat
    assert "gemini" in cat
    assert "ollama" in cat
    assert "llama_cpp" in cat
    assert all(isinstance(e, CatalogEntry) for e in cat["anthropic"])
    assert len(cat["anthropic"]) >= 2  # shipped default has 3 entries


def test_default_catalog_entry_fields():
    path = Path("/tmp/_arc_test_default.yml")
    path.write_text(DEFAULT_CATALOG_YAML)
    try:
        cat = load_catalog(path)
        first = cat["anthropic"][0]
        assert first.id.startswith("claude-")
        assert first.label
    finally:
        path.unlink(missing_ok=True)


# ── User-edited catalog ────────────────────────────────────────────────────


def test_user_catalog_merges_with_default_for_missing_providers(tmp_path: Path):
    """User omits gemini section → default's gemini entries fill in."""
    user_catalog = """
anthropic:
  - id: claude-haiku-4-5
    label: "haiku only"
"""
    path = tmp_path / "catalog.yml"
    path.write_text(user_catalog)
    cat = load_catalog(path)
    # User's anthropic section replaces shipped one
    assert [e.id for e in cat["anthropic"]] == ["claude-haiku-4-5"]
    # Shipped gemini entries still present (user didn't override the key)
    assert len(cat["gemini"]) >= 2


def test_user_can_add_new_entries(tmp_path: Path):
    user_catalog = """
anthropic:
  - id: claude-opus-4-7
    label: "Opus 4.7"
  - id: claude-sonnet-4-6
    label: "Sonnet 4.6"
  - id: my-finetune
    label: "Custom finetune"
    note: "internal"
"""
    path = tmp_path / "catalog.yml"
    path.write_text(user_catalog)
    cat = load_catalog(path)
    assert [e.id for e in cat["anthropic"]] == [
        "claude-opus-4-7", "claude-sonnet-4-6", "my-finetune",
    ]
    assert cat["anthropic"][2].note == "internal"


# ── Failure modes ──────────────────────────────────────────────────────────


def test_missing_file_returns_shipped_default(tmp_path: Path, caplog):
    path = tmp_path / "nope.yml"
    with caplog.at_level("WARNING", logger="arc.setup.catalog"):
        cat = load_catalog(path)
    assert "anthropic" in cat
    assert any("missing" in r.message for r in caplog.records)


def test_malformed_yaml_returns_shipped_default(tmp_path: Path, caplog):
    path = tmp_path / "broken.yml"
    path.write_text("anthropic: [not closed")
    with caplog.at_level("WARNING", logger="arc.setup.catalog"):
        cat = load_catalog(path)
    assert "anthropic" in cat
    assert any("not valid YAML" in r.message for r in caplog.records)


def test_top_level_non_mapping_returns_default(tmp_path: Path, caplog):
    path = tmp_path / "bad.yml"
    path.write_text("- one\n- two\n")
    with caplog.at_level("WARNING", logger="arc.setup.catalog"):
        cat = load_catalog(path)
    assert "anthropic" in cat


def test_entry_missing_id_raises_catalog_error(tmp_path: Path):
    path = tmp_path / "catalog.yml"
    path.write_text("""
anthropic:
  - label: "missing id"
""")
    with pytest.raises(CatalogError, match=r"anthropic\[0\] missing required field 'id'"):
        load_catalog(path)


def test_entry_missing_label_raises_catalog_error(tmp_path: Path):
    path = tmp_path / "catalog.yml"
    path.write_text("""
gemini:
  - id: gemini-2.5-pro
""")
    with pytest.raises(CatalogError, match=r"gemini\[0\] missing required field 'label'"):
        load_catalog(path)


def test_empty_id_raises(tmp_path: Path):
    path = tmp_path / "catalog.yml"
    path.write_text("""
anthropic:
  - id: ""
    label: empty
""")
    with pytest.raises(CatalogError, match="non-empty string"):
        load_catalog(path)


def test_empty_provider_list_is_allowed(tmp_path: Path):
    path = tmp_path / "catalog.yml"
    path.write_text("""
ollama: []
""")
    cat = load_catalog(path)
    assert cat["ollama"] == []


def test_null_provider_treated_as_empty(tmp_path: Path):
    path = tmp_path / "catalog.yml"
    path.write_text("""
llama_cpp: ~
""")
    cat = load_catalog(path)
    assert cat["llama_cpp"] == []


# ── Manual sentinel ────────────────────────────────────────────────────────


def test_append_manual_sentinel_adds_to_empty_list():
    out = append_manual_sentinel([])
    assert len(out) == 1
    assert out[0].id == MANUAL_ENTRY_ID


def test_append_manual_sentinel_is_idempotent():
    base = [
        CatalogEntry(id="x", label="X"),
        CatalogEntry(id=MANUAL_ENTRY_ID, label="…"),
    ]
    out = append_manual_sentinel(base)
    assert out == base


def test_append_manual_sentinel_preserves_existing_entries():
    base = [CatalogEntry(id="x", label="X")]
    out = append_manual_sentinel(base)
    assert out[:1] == base
    assert out[-1].id == MANUAL_ENTRY_ID
