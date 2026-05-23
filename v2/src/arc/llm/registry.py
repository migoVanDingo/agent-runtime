"""`~/.arc/llm_servers.yml` loader for `arc llm` lifecycle commands.

See _design/0018-llm-server-lifecycle.md.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class RegistryError(ValueError):
    """`llm_servers.yml` is malformed or references a missing model."""


# ── Argv translation ──────────────────────────────────────────────────────
#
# llama.cpp's `llama-server` uses short flags (-m, -c, -ngl, --host, --port).
# llama-cpp-python's `python -m llama_cpp.server` uses long-form options
# (--model, --n-ctx, --n-gpu-layers, --host, --port).  When a registry
# entry says `kind: llama_cpp_python`, we map the common short flags so
# users can write one set of args and have it work for either backend.

_LLAMA_CPP_PYTHON_ARG_MAP: dict[str, str] = {
    "-m": "--model",
    "-c": "--n-ctx",
    "-ngl": "--n-gpu-layers",
    "-t": "--n-threads",
    "-b": "--n-batch",
}


@dataclass(frozen=True)
class ServerBinary:
    """Where the inference server lives and which CLI dialect it speaks."""
    path: str       # absolute path OR a name on $PATH ("python", "llama-server")
    kind: str       # "llama_cpp" | "llama_cpp_python"

    def validate(self) -> None:
        if self.kind not in ("llama_cpp", "llama_cpp_python"):
            raise RegistryError(
                f"binary.kind must be 'llama_cpp' or 'llama_cpp_python', got {self.kind!r}"
            )


@dataclass(frozen=True)
class ServerModel:
    """One pickable .gguf model + the extra args needed to load it."""
    id: str
    label: str
    gguf: Path
    extra_args: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Registry:
    """Parsed llm_servers.yml.  Use Registry.find(id) to resolve a model."""
    binary: ServerBinary
    default_args: list[str]
    startup_timeout_seconds: int
    models: list[ServerModel]
    source_path: Path

    def find(self, model_id: str) -> ServerModel:
        for m in self.models:
            if m.id == model_id:
                return m
        known = ", ".join(m.id for m in self.models) or "(none)"
        raise RegistryError(
            f"model {model_id!r} not in {self.source_path}.  Known ids: {known}"
        )

    def build_argv(self, model: ServerModel) -> list[str]:
        """Return the full argv to pass to `subprocess.Popen` for this model."""
        if self.binary.kind == "llama_cpp":
            return [
                self.binary.path,
                "-m", str(model.gguf),
                *self.default_args,
                *model.extra_args,
            ]
        # llama_cpp_python
        translated_defaults = _translate_args(self.default_args)
        translated_extras = _translate_args(model.extra_args)
        return [
            self.binary.path,
            "-m", "llama_cpp.server",
            "--model", str(model.gguf),
            *translated_defaults,
            *translated_extras,
        ]


# ── Loader ─────────────────────────────────────────────────────────────────


def load_registry(path: Path) -> Registry:
    """Parse llm_servers.yml.  Raises RegistryError on any structural issue."""
    if not path.exists():
        raise RegistryError(
            f"llm_servers.yml not found at {path}\n"
            f"  run `arc bootstrap` to create one, then edit it to add models."
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RegistryError(f"llm_servers.yml at {path} is not valid YAML:\n  {e}") from e

    if not isinstance(data, dict):
        raise RegistryError(
            f"llm_servers.yml at {path} must be a YAML mapping at the top level"
        )

    binary = _parse_binary(data.get("binary"), path)
    default_args = _parse_str_list(data.get("default_args") or [], "default_args")
    startup_timeout = int(data.get("startup_timeout_seconds") or 120)
    models = _parse_models(data.get("models") or [])
    return Registry(
        binary=binary,
        default_args=default_args,
        startup_timeout_seconds=startup_timeout,
        models=models,
        source_path=path,
    )


# ── Section parsers ───────────────────────────────────────────────────────


def _parse_binary(d, source: Path) -> ServerBinary:
    if not isinstance(d, dict):
        raise RegistryError(f"llm_servers.yml at {source}: missing `binary:` block")
    try:
        raw_path = d["path"]
        kind = d["kind"]
    except KeyError as e:
        raise RegistryError(
            f"llm_servers.yml at {source}: binary.{e.args[0]} is required"
        ) from None
    expanded = os.path.expandvars(os.path.expanduser(str(raw_path)))
    binary = ServerBinary(path=expanded, kind=str(kind))
    binary.validate()
    return binary


def _parse_models(items) -> list[ServerModel]:
    if not isinstance(items, list):
        raise RegistryError("models must be a list (use [] if you have none yet)")
    out: list[ServerModel] = []
    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            raise RegistryError(f"models[{i}] must be a mapping")
        try:
            mid = raw["id"]
            label = raw["label"]
            gguf = raw["gguf"]
        except KeyError as e:
            raise RegistryError(
                f"models[{i}] missing required field {e.args[0]!r}"
            ) from None
        gguf_path = Path(os.path.expandvars(os.path.expanduser(str(gguf))))
        extra_args = _parse_str_list(raw.get("extra_args") or [], f"models[{i}].extra_args")
        out.append(ServerModel(
            id=str(mid),
            label=str(label),
            gguf=gguf_path,
            extra_args=extra_args,
        ))
    return out


def _parse_str_list(items, what: str) -> list[str]:
    if not isinstance(items, list):
        raise RegistryError(f"{what} must be a list of strings")
    return [str(x) for x in items]


# ── Arg translation ───────────────────────────────────────────────────────


def _translate_args(args: list[str]) -> list[str]:
    """Map llama.cpp short flags to llama-cpp-python long-form options."""
    out: list[str] = []
    for a in args:
        out.append(_LLAMA_CPP_PYTHON_ARG_MAP.get(a, a))
    return out
