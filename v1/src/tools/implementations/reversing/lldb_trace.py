"""lldb_trace — run a binary under LLDB, capture register state at breakpoints.

Non-interactive: generates a command script, runs LLDB in batch mode, parses output.
ASLR is disabled so addresses from Ghidra match runtime addresses exactly.

Output is intentionally small (~200 chars per breakpoint hit) so it never triggers
the artifact pager and the agent always sees the actual register values.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight

_DEFAULT_REGS = ["rax", "rbx", "rcx", "rdx", "rdi", "rsi",
                 "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15"]

_NOISE_RE = re.compile(
    r"^\(lldb\)|^Current executable|^Breakpoint \d+ created|"
    r"^Process \d+|^Target \d+|^thread #|^frame #|"
    r"^lldb|^warning:|^\s*$"
)


def _build_script(path: str, args: list[str], breakpoints: list[str],
                  registers: list[str], memory_reads: list[dict],
                  max_hits: int) -> str:
    """Generate the LLDB batch command script.

    Uses --stop-at-entry so the process halts before executing any code,
    then sets breakpoints by address/name and continues to each one in order,
    dumping registers at each stop. This is more reliable than breakpoint
    commands in batch mode, which can be swallowed silently.
    """
    # Strip null bytes — they corrupt the LLDB command file (written as text)
    # and terminate shell arguments early when embedded in the launch command.
    clean_args = [a.replace('\x00', '') for a in args]
    arg_str = " ".join(f'"{a}"' for a in clean_args) if clean_args else ""
    lines = [
        "settings set target.disable-aslr true",
        f'target create "{path}"',
        f"process launch --stop-at-entry -- {arg_str}".strip(),
    ]

    for i, bp in enumerate(breakpoints, start=1):
        bp = bp.strip()
        if bp.startswith("0x") or re.match(r"^\d+$", bp):
            lines.append(f"breakpoint set --address {bp}")
        else:
            lines.append(f'breakpoint set --name "{bp}"')

    reg_cmd = "register read " + " ".join(registers)
    mem_cmds = [
        f"memory read --size 1 --count {int(m.get('size', 16))} --format hex {m.get('expr', '$rdi')}"
        for m in memory_reads
    ]

    for _ in range(len(breakpoints) * max(1, max_hits)):
        lines.append("continue")
        lines.append(reg_cmd)
        for mc in mem_cmds:
            lines.append(mc)

    lines.append("quit")
    return "\n".join(lines)


def _run_lldb(path: str, script: str, timeout: int) -> str:
    if not shutil.which("lldb"):
        return "Error: lldb not found in PATH. Install Xcode command-line tools: xcode-select --install"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".lldb", delete=False) as f:
        f.write(script)
        script_path = f.name

    try:
        result = subprocess.run(
            ["lldb", "--batch", "-s", script_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return f"Error: lldb timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"
    finally:
        os.unlink(script_path)


def _parse_output(raw: str, breakpoints: list[str], registers: list[str]) -> str:
    """Strip LLDB noise and format register captures cleanly."""
    sections: list[str] = []
    current_label: str | None = None
    current_lines: list[str] = []
    hit_counts: dict[str, int] = {}

    # Matches both "stop reason = breakpoint N.M" and the address line
    bp_stop_re = re.compile(r"stop reason = breakpoint (\d+)")
    addr_re = re.compile(r"frame #0.*?0x([0-9a-f]+)")
    reg_re = re.compile(r"^\s+(\w+)\s*=\s*(0x[0-9a-f]+(?:\s+\S+)?|\d+)")
    mem_re = re.compile(r"^\s*0x[0-9a-f]+:", re.IGNORECASE)

    for line in raw.splitlines():
        m = bp_stop_re.search(line)
        if m:
            if current_label and current_lines:
                sections.append(f"[{current_label}]\n" + "\n".join(current_lines))
            bp_num = int(m.group(1))
            idx = bp_num - 1
            label = breakpoints[idx] if idx < len(breakpoints) else f"bp{bp_num}"
            hit_counts[label] = hit_counts.get(label, 0) + 1
            current_label = f"breakpoint {label} — hit {hit_counts[label]}"
            current_lines = []
            continue

        # Also pick up address from frame line for better labeling
        fa = addr_re.search(line)
        if fa and current_label and "0x" not in current_label:
            current_label = f"{current_label} @ 0x{fa.group(1)}"
            continue

        if _NOISE_RE.match(line):
            continue

        rm = reg_re.match(line)
        if rm:
            reg = rm.group(1).lower()
            val = rm.group(2).split()[0]  # strip annotation like "libsystem_c`strlen"
            if reg in registers:
                if current_label is None:
                    current_label = "entry stop"
                    current_lines = []
                current_lines.append(f"  {reg}={val}")
            continue

        if mem_re.match(line):
            if current_label is not None:
                current_lines.append(f"  mem: {line.strip()}")

    if current_label and current_lines:
        sections.append(f"[{current_label}]\n" + "\n".join(current_lines))

    if not sections:
        cleaned = [ln for ln in raw.splitlines() if not _NOISE_RE.match(ln) and ln.strip()]
        return "\n".join(cleaned[:60]) or "(no breakpoints hit — check addresses and args)"

    return "\n\n".join(sections)


class LLDBTraceTool(BaseTool):
    name = "lldb_trace"
    description = (
        "Run a binary under LLDB and capture register state at specified breakpoints. "
        "Non-interactive — results returned as structured register dumps. "
        "ASLR is disabled so addresses from Ghidra match runtime exactly. "
        "Use this to observe concrete register values at key points in execution "
        "(function entry/exit, loop boundaries, cipher rounds). "
        "Output is small (~200 chars per hit) — no risk of token overflow. "
        "Provide addresses as hex strings (e.g. '0x100000460') or symbol names (e.g. 'main')."
    )
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(
                    type="string",
                    description="Path to the binary",
                ),
                "args": ToolProperty(
                    type="array",
                    description="Command-line arguments for the binary (e.g. [\"-e\", \"secret\", \"hello\"])",
                    items={"type": "string"},
                ),
                "breakpoints": ToolProperty(
                    type="array",
                    description="Addresses (\"0x100000460\") or symbol names (\"main\") where execution will pause",
                    items={"type": "string"},
                ),
                "registers": ToolProperty(
                    type="array",
                    description="Registers to capture at each breakpoint (default: rax rbx rcx rdx rdi rsi r8-r15)",
                    items={"type": "string"},
                ),
                "memory": ToolProperty(
                    type="array",
                    description="Optional memory regions to dump: [{\"expr\": \"$rdi\", \"size\": 16}]",
                    items={"type": "object"},
                ),
                "max_hits": ToolProperty(
                    type="string",
                    description="Max times to capture each breakpoint (default 1)",
                ),
                "timeout": ToolProperty(
                    type="string",
                    description="Timeout in seconds (default 30)",
                ),
            },
            required=["path", "breakpoints"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        args = list(tool_input.get("args") or [])
        breakpoints = list(tool_input.get("breakpoints") or [])
        registers = list(tool_input.get("registers") or _DEFAULT_REGS)
        memory = list(tool_input.get("memory") or [])
        max_hits = int(tool_input.get("max_hits") or 1)
        timeout = int(tool_input.get("timeout") or 30)

        if not breakpoints:
            return "Error: at least one breakpoint address or symbol name is required"

        script = _build_script(path, args, breakpoints, registers, memory, max_hits)
        raw = _run_lldb(path, script, timeout)
        return _parse_output(raw, breakpoints, registers)
