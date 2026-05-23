"""Unit tests for `arc.llm.registry`."""
from __future__ import annotations

from pathlib import Path

import pytest

from arc.defaults import DEFAULT_LLM_SERVERS_YAML
from arc.llm.registry import (
    Registry,
    RegistryError,
    ServerBinary,
    ServerModel,
    load_registry,
)


# ── Default file parses ───────────────────────────────────────────────────


def test_shipped_default_parses(tmp_path: Path):
    path = tmp_path / "llm_servers.yml"
    path.write_text(DEFAULT_LLM_SERVERS_YAML)
    reg = load_registry(path)
    assert reg.binary.kind in ("llama_cpp", "llama_cpp_python")
    assert reg.startup_timeout_seconds > 0
    # Empty models list is fine; user adds entries later.
    assert reg.models == []


# ── Loader basics ──────────────────────────────────────────────────────────


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "llm_servers.yml"
    p.write_text(text)
    return p


def test_parses_typical_config(tmp_path: Path):
    p = _write(tmp_path, """
binary:
  path: /usr/local/bin/llama-server
  kind: llama_cpp
default_args: ["--host", "127.0.0.1", "--port", "8080"]
startup_timeout_seconds: 60
models:
  - id: llama-3.1-8b
    label: "Llama 3.1 8B"
    gguf: /models/llama.gguf
    extra_args: ["-c", "8192"]
""")
    reg = load_registry(p)
    assert reg.binary.path == "/usr/local/bin/llama-server"
    assert reg.binary.kind == "llama_cpp"
    assert reg.default_args == ["--host", "127.0.0.1", "--port", "8080"]
    assert reg.startup_timeout_seconds == 60
    assert len(reg.models) == 1
    m = reg.models[0]
    assert m.id == "llama-3.1-8b"
    assert m.gguf == Path("/models/llama.gguf")
    assert m.extra_args == ["-c", "8192"]


def test_tilde_in_gguf_path_is_expanded(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", "/home/test")
    p = _write(tmp_path, """
binary:
  path: ~/llama.cpp/bin/llama-server
  kind: llama_cpp
default_args: []
models:
  - id: m
    label: M
    gguf: ~/models/m.gguf
""")
    reg = load_registry(p)
    assert str(reg.binary.path) == "/home/test/llama.cpp/bin/llama-server"
    assert str(reg.models[0].gguf) == "/home/test/models/m.gguf"


def test_missing_file_raises_with_hint(tmp_path: Path):
    with pytest.raises(RegistryError, match="not found"):
        load_registry(tmp_path / "missing.yml")


def test_malformed_yaml_raises(tmp_path: Path):
    p = _write(tmp_path, "binary: [unclosed")
    with pytest.raises(RegistryError, match="not valid YAML"):
        load_registry(p)


def test_missing_binary_block_raises(tmp_path: Path):
    p = _write(tmp_path, "default_args: []\nmodels: []\n")
    with pytest.raises(RegistryError, match="missing `binary:`"):
        load_registry(p)


def test_invalid_binary_kind_raises(tmp_path: Path):
    p = _write(tmp_path, """
binary:
  path: /usr/bin/something
  kind: vllm
models: []
""")
    with pytest.raises(RegistryError, match="binary.kind"):
        load_registry(p)


def test_model_missing_id_raises(tmp_path: Path):
    p = _write(tmp_path, """
binary: {path: x, kind: llama_cpp}
models:
  - label: nope
    gguf: /tmp/x.gguf
""")
    with pytest.raises(RegistryError, match="missing required field 'id'"):
        load_registry(p)


# ── Lookup ─────────────────────────────────────────────────────────────────


def _registry_with_models() -> Registry:
    return Registry(
        binary=ServerBinary(path="llama-server", kind="llama_cpp"),
        default_args=["--port", "8080"],
        startup_timeout_seconds=60,
        models=[
            ServerModel(id="a", label="A", gguf=Path("/m/a.gguf")),
            ServerModel(id="b", label="B", gguf=Path("/m/b.gguf"),
                        extra_args=["-c", "8192"]),
        ],
        source_path=Path("/tmp/test.yml"),
    )


def test_find_returns_model():
    reg = _registry_with_models()
    m = reg.find("b")
    assert m.id == "b"
    assert m.extra_args == ["-c", "8192"]


def test_find_unknown_raises_with_known_ids():
    reg = _registry_with_models()
    with pytest.raises(RegistryError, match=r"Known ids: a, b"):
        reg.find("c")


# ── Argv builder ──────────────────────────────────────────────────────────


def test_argv_for_llama_cpp_kind():
    reg = _registry_with_models()
    m = reg.find("a")
    argv = reg.build_argv(m)
    assert argv == [
        "llama-server", "-m", "/m/a.gguf", "--port", "8080",
    ]


def test_argv_for_llama_cpp_kind_with_extra_args():
    reg = _registry_with_models()
    m = reg.find("b")
    argv = reg.build_argv(m)
    assert argv == [
        "llama-server", "-m", "/m/b.gguf", "--port", "8080", "-c", "8192",
    ]


def test_argv_for_llama_cpp_python_translates_short_flags():
    reg = Registry(
        binary=ServerBinary(path="python", kind="llama_cpp_python"),
        default_args=["--host", "127.0.0.1", "--port", "8080"],
        startup_timeout_seconds=60,
        models=[ServerModel(
            id="m", label="M", gguf=Path("/m/x.gguf"),
            extra_args=["-c", "8192", "-ngl", "99"],
        )],
        source_path=Path("/tmp/test.yml"),
    )
    argv = reg.build_argv(reg.find("m"))
    assert argv == [
        "python", "-m", "llama_cpp.server",
        "--model", "/m/x.gguf",
        "--host", "127.0.0.1", "--port", "8080",
        "--n-ctx", "8192", "--n-gpu-layers", "99",
    ]


def test_argv_for_llama_cpp_python_passes_unknown_args_unchanged():
    reg = Registry(
        binary=ServerBinary(path="python", kind="llama_cpp_python"),
        default_args=[],
        startup_timeout_seconds=60,
        models=[ServerModel(
            id="m", label="M", gguf=Path("/m/x.gguf"),
            extra_args=["--exotic-flag", "value"],
        )],
        source_path=Path("/tmp/test.yml"),
    )
    argv = reg.build_argv(reg.find("m"))
    assert "--exotic-flag" in argv
    assert "value" in argv
