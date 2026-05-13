"""Tool-related configuration dataclasses (radare2, ghidra, angr, tool policy)."""
from dataclasses import dataclass, field


@dataclass
class Radare2Config:
    timeout_seconds: int = 30


@dataclass
class GhidraConfig:
    # Empty default — when blank, resolves at runtime to <ARC_HOME>/ghidra/projects/
    # via session_paths.ghidra_projects_dir(). Set explicitly only to override.
    project_dir: str = ""
    timeout_seconds: int = 120
    scripts_dir: str = "src/tools/implementations/reversing/ghidra_scripts"


@dataclass
class AngrConfig:
    # Per-tool timeouts (seconds); binary complexity multiplier applied at runtime
    timeout_reachable: int = 60
    timeout_solve: int = 120
    timeout_constraints: int = 120
    timeout_explore: int = 300
    # Function-count thresholds for complexity scaling
    complexity_medium_threshold: int = 50   # >=50 fns → 1.5× timeout
    complexity_large_threshold: int = 200   # >=200 fns → 2.5× timeout


@dataclass
class ToolsConfig:
    strings_min_length: str
    hexdump_default_bytes: str
    radare2: Radare2Config = None  # type: ignore[assignment]
    ghidra: GhidraConfig = None    # type: ignore[assignment]
    angr: AngrConfig = None        # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.radare2 is None:
            self.radare2 = Radare2Config()
        if self.ghidra is None:
            self.ghidra = GhidraConfig()
        if self.angr is None:
            self.angr = AngrConfig()


@dataclass
class ToolPolicyConfig:
    """Infrastructure policy for tool exposure to step execution.

    utility_tools: when a step's base tool is the key, the value's tools
    are also exposed. Data-driven so adding a new relationship doesn't
    require editing code.
    """
    utility_tools: dict[str, list[str]] = field(default_factory=dict)
