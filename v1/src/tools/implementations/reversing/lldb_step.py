"""lldb_step — run to an address then step N instructions, capturing registers after each.

Used for fine-grained observation of inner loops (cipher rounds, key schedule).
Each step produces one register snapshot — the agent can watch exact values change
instruction by instruction and deduce the operation being performed.

Output: one register line per step, labelled by instruction address.
Typically 10-30 steps is enough to characterize a cipher round.
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
    r"^Process \d+|^Target \d+|^thread #|^lldb|^warning:|^\s*$"
)


def _build_step_script(path: str, args: list[str], start: str,
                       steps: int, registers: list[str]) -> str:
    # Strip null bytes — they corrupt the LLDB command file written to disk.
    clean_args = [a.replace('\x00', '') for a in args]
    arg_str = " ".join(f'"{a}"' for a in clean_args) if clean_args else ""
    lines = [
        "settings set target.disable-aslr true",
        f'target create "{path}"',
        f"process launch --stop-at-entry -- {arg_str}".strip(),
    ]

    start = start.strip()
    if start.startswith("0x") or re.match(r"^\d+$", start):
        lines.append(f"breakpoint set --address {start}")
    else:
        lines.append(f'breakpoint set --name "{start}"')

    lines.append("continue")

    # After hitting the start breakpoint, step N times with register dumps
    reg_cmd = "register read " + " ".join(registers)
    lines.append(reg_cmd)
    for _ in range(steps):
        lines.append("thread step-inst")
        lines.append(reg_cmd)

    lines.append("quit")
    return "\n".join(lines)


def _run_lldb(script: str, timeout: int) -> str:
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


def _parse_step_output(raw: str, registers: list[str]) -> str:
    """Format as numbered steps with register snapshots.

    Handles two LLDB stop formats:
      1. Disassembly arrow:  ->  0xXXX <+N>: instruction
      2. Frame summary:      frame #0: 0xXXX proc`sym + N  (EXC stops, breakpoint hits)
    Both emit register lines immediately after.
    """
    reg_re = re.compile(r"^\s+(\w+)\s*=\s*(0x[0-9a-f]+(?:\s+\S+)?|\d+)", re.IGNORECASE)
    inst_re = re.compile(r"->\s+(0x[0-9a-f]+)[^:]*:?\s+(.+)", re.IGNORECASE)
    frame_re = re.compile(r"frame #\d+:\s+(0x[0-9a-f]+)\s+\S+", re.IGNORECASE)
    exc_re = re.compile(r"EXC_BAD_INSTRUCTION|SIGSEGV|SIGBUS|EXC_BAD_ACCESS", re.IGNORECASE)

    steps = []
    current_regs: dict[str, str] = {}
    current_addr = ""
    current_inst = ""
    step_num = 0
    had_exception = False

    for line in raw.splitlines():
        if _NOISE_RE.match(line):
            continue

        if exc_re.search(line):
            had_exception = True

        m_inst = inst_re.search(line)
        if m_inst:
            if current_regs:
                reg_str = "  ".join(
                    f"{r}={current_regs[r].split()[0]}" for r in registers if r in current_regs
                )
                steps.append(f"step {step_num:2d} @ {current_addr}  {current_inst}\n    {reg_str}")
                current_regs = {}
                step_num += 1
            current_addr = m_inst.group(1)
            current_inst = m_inst.group(2).strip()
            continue

        m_frame = frame_re.search(line)
        if m_frame:
            if current_regs:
                reg_str = "  ".join(
                    f"{r}={current_regs[r].split()[0]}" for r in registers if r in current_regs
                )
                steps.append(f"step {step_num:2d} @ {current_addr}  {current_inst}\n    {reg_str}")
                current_regs = {}
                step_num += 1
            current_addr = m_frame.group(1)
            current_inst = "(stopped)"
            continue

        m_reg = reg_re.match(line)
        if m_reg:
            reg = m_reg.group(1).lower()
            if reg in registers:
                current_regs[reg] = m_reg.group(2)

    if current_regs:
        reg_str = "  ".join(
            f"{r}={current_regs[r].split()[0]}" for r in registers if r in current_regs
        )
        steps.append(f"step {step_num:2d} @ {current_addr}  {current_inst}\n    {reg_str}")

    if not steps:
        cleaned = [ln for ln in raw.splitlines() if not _NOISE_RE.match(ln) and ln.strip()]
        return "\n".join(cleaned[:60]) or "(no steps captured — check start address and args)"

    result = "\n".join(steps)
    if had_exception:
        result += (
            "\n\nNote: EXC_BAD_INSTRUCTION at breakpoint — LLDB's software trap may conflict "
            "with the instruction at this address. Try stepping from an earlier address "
            "(e.g. 'main' or 0x1000004c0) and step forward to the cipher loop."
        )
    return result


class LLDBStepTool(BaseTool):
    name = "lldb_step"
    description = (
        "Run a binary to a starting address then step N instructions, capturing register "
        "state after each step. "
        "Use this to observe exactly what an inner loop (cipher round, key schedule) does "
        "instruction by instruction. "
        "ASLR is disabled so Ghidra addresses match runtime exactly. "
        "10-30 steps is usually enough to characterize a cipher round. "
        "Output: one register snapshot per step, small enough to read directly."
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
                    description="Command-line arguments for the binary",
                    items={"type": "string"},
                ),
                "start": ToolProperty(
                    type="string",
                    description="Address (\"0x100000610\") or symbol (\"entry\") to run to before stepping",
                ),
                "steps": ToolProperty(
                    type="string",
                    description="Number of instructions to step (default 20)",
                ),
                "registers": ToolProperty(
                    type="array",
                    description="Registers to capture after each step (default: rax rbx rcx rdx rdi rsi r8-r15)",
                    items={"type": "string"},
                ),
                "timeout": ToolProperty(
                    type="string",
                    description="Timeout in seconds (default 60)",
                ),
            },
            required=["path", "start"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        args = list(tool_input.get("args") or [])
        start = str(tool_input.get("start", "main"))
        steps = int(tool_input.get("steps") or 20)
        registers = list(tool_input.get("registers") or _DEFAULT_REGS)
        timeout = int(tool_input.get("timeout") or 60)

        script = _build_step_script(path, args, start, steps, registers)
        raw = _run_lldb(script, timeout)
        return _parse_step_output(raw, registers)
