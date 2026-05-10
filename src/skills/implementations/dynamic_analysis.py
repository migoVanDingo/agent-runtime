"""Dynamic analysis skill — Ghidra for WHERE, LLDB for WHAT.

Workflow:
  1. Ghidra identifies functions and their addresses (structure)
  2. Ghidra decompiles ONE target function (address hints for breakpoints)
  3. lldb_trace x2 — differential: what changes between two inputs = data-dependent
  4. lldb_step — walk the inner loop instruction by instruction
  5. Synthesize: write code from observed behavior (not inferred structure)

This is the correct approach when:
  - Static decompile was too noisy or too large to process
  - A known oracle input/output pair exists (enables verification)
  - The goal is code reconstruction, not just understanding

Output token cost for the dynamic phase: ~1-2k chars total. No 429 risk.
"""
from __future__ import annotations

import os
import re
from planning.schema import Step, ActionType
from skills.base import Skill, SkillContext
from skills.criteria import LLMJudgedCriteria
from runtime.schema import ContinuationDecision


class DynamicAnalysis(Skill):
    """Trace binary execution with LLDB to reconstruct behavior from runtime values."""

    name = "dynamic-analysis"
    intent = (
        "Use this skill when the user wants to understand what a binary does at runtime — "
        "trace execution with specific inputs, inspect register values at breakpoints, "
        "or reconstruct source code from observed behavior rather than static decompilation. "
        "Preferred over deep-disassembly when: (a) prior static decompile was too noisy "
        "or too large; (b) the goal is code reconstruction and a known oracle input/output "
        "pair exists; (c) the user wants to verify a hypothesis about the algorithm with "
        "concrete runtime data; (d) the binary uses a custom cipher or non-standard algorithm "
        "that Ghidra's decompile does not clearly express."
    )

    pattern = re.compile(
        r"(?:trace|step through|register|breakpoint|runtime|dynamic|lldb|gdb|"
        r"watch execution|debug|what does .{0,30} do at runtime|"
        r"reconstruct from .{0,20}behavior)",
        re.IGNORECASE,
    )

    _output_re = re.compile(r"(\S+\.(?:c|h|cpp|py|rs|go|java|swift|js|ts|md|txt|json))\b")
    _target_re = re.compile(r"(?:^|\s)(_?[\w./\-]+(?:/[\w.\-]+)*)\b")
    _oracle_re = re.compile(
        r"(?:encrypt|run|input)\s+['\"]?(\S+)['\"]?\s+(?:gives?|→|->|outputs?|produces?|=+)\s+['\"]?([0-9a-fA-F]+)['\"]?",
        re.IGNORECASE,
    )

    @property
    def completion_criteria(self):
        return LLMJudgedCriteria(
            prompt=(
                "Did the agent produce a concrete result from dynamic analysis — "
                "either reconstructed source code that matches the oracle output, "
                "or a detailed technical report explaining what was learned from "
                "the register traces and step output?"
            ),
            on_met=ContinuationDecision.SYNTHESIZE,
        )

    def expand(self, ctx: SkillContext) -> list[Step]:
        message = ctx.original_query
        target = self._extract_target(message)
        basename = os.path.basename(target).replace(".", "_")
        output = self._extract_output(message)
        oracle = self._extract_oracle(message)
        synthesis_desc = self._infer_synthesis(message, basename, output, oracle)
        n = ctx.starting_step_number

        # Use ./target for bare names to prevent the LLM from confusing the binary
        # with same-named paths in _analysis/ (e.g. _analysis/proc/...).
        binary_ref = target if "/" in target else f"./{target}"

        # Phase 1: Structure — identify functions and find cipher/key function address
        struct_steps = [
            Step(step=n,
                 description=(
                     f"Run Ghidra analysis on the binary executable {binary_ref} "
                     f"using ghidra_analyze to build the project cache. "
                     f"The target is the executable file itself, NOT any file under _analysis/."
                 ),
                 action_type=ActionType.REVERSING, tool="ghidra_analyze"),
            Step(step=n + 1,
                 description=(
                     f"List all functions in {binary_ref} using ghidra_functions. "
                     f"Identify which function(s) are likely the cipher, key setup, or "
                     f"main processing routine based on their names and addresses."
                 ),
                 action_type=ActionType.REVERSING, tool="ghidra_functions"),
            Step(step=n + 2,
                 description=(
                     f"Decompile ONLY the main cipher or processing function of {binary_ref} "
                     f"using ghidra_decompile with the 'function' argument set to the target "
                     f"function name or address identified in the previous step. "
                     f"Do NOT decompile all functions — just the one cipher/processing function. "
                     f"Goal: identify the inner loop address for LLDB breakpoints."
                 ),
                 action_type=ActionType.REVERSING, tool="ghidra_decompile"),
        ]

        # Phase 2: Dynamic traces — two inputs for differential analysis
        input1, input2 = self._pick_trace_inputs(oracle)
        trace_steps = [
            Step(step=n + 3,
                 description=(
                     f"Trace the binary {binary_ref} with lldb_trace using args {input1}. "
                     f"Set breakpoints at: (1) the entry point or main function, "
                     f"(2) the cipher function entry identified from ghidra_functions. "
                     f"Capture registers rax, rbx, rcx, rdx, rdi, rsi, r8, r9, r10, r11, r12, r13, r14, r15. "
                     f"This is trace #1 — record all register values at each breakpoint hit."
                 ),
                 action_type=ActionType.REVERSING, tool="lldb_trace"),
            Step(step=n + 4,
                 description=(
                     f"Trace {binary_ref} again with lldb_trace using args {input2}. "
                     f"Use the SAME breakpoints as trace #1. "
                     f"This is trace #2 — compare with trace #1: "
                     f"registers that differ = input/output data. "
                     f"Registers that are identical = key material, constants, or IV."
                 ),
                 action_type=ActionType.REVERSING, tool="lldb_trace"),
        ]

        # Phase 3: Step trace — walk the inner loop
        step_steps = [
            Step(step=n + 5,
                 description=(
                     f"Use lldb_step on the binary {binary_ref} with args {input1}. "
                     f"Set 'start' to 'main' (the binary's entry point). "
                     f"Step 32 instructions from there. "
                     f"Note: if EXC_BAD_INSTRUCTION appears, the register values are still "
                     f"valid — read them. They reveal cipher state at that point. "
                     f"Observe how register values change with each instruction — "
                     f"this reveals the exact operations: add vs XOR, shift direction, "
                     f"which key word is loaded, round count increment."
                 ),
                 action_type=ActionType.REVERSING, tool="lldb_step"),
        ]

        # Phase 4: Synthesis
        synthesis_num = n + 6
        synthesis_steps = [
            Step(step=synthesis_num,
                 description=(
                     f"{synthesis_desc}\n\n"
                     f"Use the differential register analysis from steps {n+3} and {n+4} to "
                     f"identify: key material (same across both traces), plaintext/ciphertext "
                     f"(different), constants (same, non-key). "
                     f"Use the step trace from step {n+5} to confirm exact operations per instruction. "
                     f"IMPORTANT: Build the implementation from observed register values, "
                     f"not from the Ghidra decompile."
                 ),
                 action_type=ActionType.CONVERSATION, tool=None),
        ]

        steps = struct_steps + trace_steps + step_steps + synthesis_steps

        if output:
            steps.append(Step(
                step=synthesis_num + 1,
                description=(
                    f"Write the reconstructed implementation to {output}. "
                    f"Content must match the file type — no markdown in code files. "
                    f"Include a comment at the top summarizing what the dynamic analysis revealed."
                ),
                action_type=ActionType.FILE_IO,
                tool="write_file",
            ))

        return steps

    # Extensions that mark an output destination, not the analysis target
    _OUTPUT_EXT_RE = re.compile(r'\.(c|cpp|py|rs|go|java|js|ts|md|txt|json|html)$', re.IGNORECASE)
    # Directory prefixes that are arc-internal output locations, not binaries
    _OUTPUT_PREFIXES = ("_analysis/", "_sessions/", "_rag/", "_store/", "_logs/", "_tests/")
    # Generic words users say when describing a binary — not actual filenames
    _GENERIC_BINARY_WORDS = frozenset({
        "executable", "binary", "program", "application", "target", "file",
        "the_binary", "binary_file", "this", "the",
    })

    def _extract_target(self, message: str) -> str:
        for m in self._target_re.finditer(message):
            tok = m.group(1)
            if self._OUTPUT_EXT_RE.search(tok):
                continue
            if any(tok.startswith(p) for p in self._OUTPUT_PREFIXES):
                continue
            if tok.lower() in self._GENERIC_BINARY_WORDS:
                continue
            if "/" in tok and not tok.startswith("http"):
                return tok
        for m in self._target_re.finditer(message):
            tok = m.group(1)
            if self._OUTPUT_EXT_RE.search(tok):
                continue
            if any(tok.startswith(p) for p in self._OUTPUT_PREFIXES):
                continue
            if tok.lower() in self._GENERIC_BINARY_WORDS:
                continue
            if re.search(r"\.\w{1,5}$|proc|bin|elf|\bexe\b", tok):
                return tok
        return "the_binary"

    def _extract_output(self, message: str) -> str | None:
        m = self._output_re.search(message)
        return m.group(1) if m else None

    def _extract_oracle(self, message: str) -> tuple[str, str] | None:
        """Extract a known input→output pair from the message for verification."""
        m = self._oracle_re.search(message)
        if m:
            return m.group(1), m.group(2)
        return None

    def _pick_trace_inputs(self, oracle: tuple[str, str] | None) -> tuple[list[str], list[str]]:
        """Choose two input sets for the differential trace.

        The binary handles its own padding — pass clean plaintext only.
        Never embed null bytes in args; they corrupt LLDB command files.
        """
        if oracle:
            inp, _ = oracle
            return (["-e", "secret", inp], ["-e", "test", inp])
        return (["-e", "secret", "hello"], ["-e", "password", "hello"])

    def _infer_synthesis(self, message: str, basename: str,
                         output: str | None, oracle: tuple | None) -> str:
        msg_lower = message.lower()
        oracle_note = ""
        if oracle:
            inp, expected = oracle
            oracle_note = (
                f"\nVerification oracle: encrypting '{inp}' must produce '{expected}'. "
                f"Test your implementation against this before writing the file."
            )

        if re.search(r"\b(?:c\+\+|cpp)\b", msg_lower):
            return f"Reconstruct equivalent C++ source from the dynamic analysis.{oracle_note}"
        if re.search(r"\bpython\b", msg_lower):
            return f"Reconstruct equivalent Python source from the dynamic analysis.{oracle_note}"
        if re.search(
            r"\b(?:reconstruct|rebuild|rewrite|clone|c\s+program|c\s+code|in\s+c\b|source\s+code)\b",
            msg_lower,
        ):
            return (
                f"Reconstruct equivalent C source code from the dynamic analysis findings. "
                f"Focus on: key derivation (which registers hold key words), "
                f"the exact round function (observed from step trace), "
                f"mode of operation (CBC vs ECB — visible in whether ciphertext blocks affect each other), "
                f"and padding (observe output size vs input size).{oracle_note}"
            )
        if re.search(r"\b(?:report|summary|analysis|audit)\b", msg_lower):
            return (
                "Write a technical analysis report from the dynamic findings: "
                "what algorithm was identified, what the register traces revealed "
                "about key schedule and round function, and what the step trace confirmed."
            )
        return (
            "Synthesize the findings from the differential register traces and step output. "
            "Identify the algorithm, key schedule, round function, and mode of operation."
            + oracle_note
        )
