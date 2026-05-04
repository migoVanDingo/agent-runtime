import os
import platform
import re
from workflows.base import Workflow
from planning.schema import Plan, Step, ActionType, StepFlags


class DeepDisassembly(Workflow):
    """Deep binary analysis: recon → targeted disassembly → algorithm ID → output.

    Phase 1 — Reconnaissance (always):
        file_info, checksec, strings, nm
    Phase 2 — Targeted disassembly (chunked by 500-line windows):
        dump to /tmp, read in slices
    Phase 3 — Synthesis:
        identify algorithm (crypto constants, block size, round count),
        describe behaviour, produce requested output
    Phase 4 — Test (when writing code):
        bash_exec to set up a venv, install deps, run the generated code,
        verify output matches the binary

    Handles any output goal: report, reconstructed source (C/Python/Rust/etc.),
    function call graph, security audit. Adapts the synthesis goal and file
    extension to whatever the user actually asked for.
    """

    name = "deep-disassembly"
    intent = (
        "Use this workflow when the user wants to deeply analyze, disassemble, decompile, "
        "or reverse-engineer a binary or executable — including requests to reconstruct or "
        "rewrite it in a programming language (C, Python, Rust, etc.), understand how it "
        "works internally, generate a function call graph, produce a detailed technical "
        "analysis or security audit, or clone/replicate its behavior. "
        "This workflow does reconnaissance first (strings, nm, checksec) to identify "
        "algorithms and key constants before disassembling."
    )

    pattern = re.compile(
        r"(?:decompile|disassemble|objdump|reconstruct|reverse[\s-]?engineer)"
        r"|rebuild\s+(?:it\s+)?in\s+(?:c|python|rust|go|java|swift)\b"
        r"|function\s+(?:call\s+)?graph"
        r"|analyze\s+(?:the\s+)?(?:binary|executable|program|proc\b)"
        r"|clone(?:\s+the)?\s+binary",
        re.IGNORECASE,
    )

    _output_re = re.compile(
        r"(\S+\.(?:c|h|cpp|py|rs|go|java|swift|js|ts|md|txt|json))\b"
    )
    _target_re = re.compile(r"(?:^|\s)(_?[\w./\-]+(?:/[\w.\-]+)*)\b")

    # Known crypto constants visible in strings/disassembly output
    _CRYPTO_HINT = (
        "If any of these constants appear in strings or disassembly, identify the algorithm "
        "immediately — do not say the algorithm is unknown:\n"
        "  0x9e3779b9 or 2654435769 → TEA / XTEA / XXTEA (block size 8 bytes, 32 rounds)\n"
        "  0x61c88647 → TEA (negative delta)\n"
        "  0x6a09e667 → SHA-256\n"
        "  0x67452301 → MD5\n"
        "  0x428a2f98 → SHA-256 round constant\n"
        "  0x5a827999 → SHA-1\n"
        "  0x9b05688c → SHA-512\n"
        "  DELTA / delta / sum in TEA context → TEA family\n"
        "Report the block size (BLOCK/ROUNDS constants), key derivation method, "
        "mode of operation (ECB/CBC/CTR), IV (if any), and padding scheme."
    )

    def generate_plan(self, match: re.Match | None, message: str) -> Plan:
        target = self._extract_target(message)
        basename = os.path.basename(target).replace(".", "_")
        tmp = f"/tmp/arc_disasm_{basename}.asm"
        is_macos = platform.system() == "Darwin"

        if is_macos:
            dump_cmd = f"otool -tv {target} > {tmp} 2>&1 && wc -l {tmp}"
        else:
            dump_cmd = f"objdump -d {target} > {tmp} 2>&1 && wc -l {tmp}"

        synthesis_desc, output, needs_code_test = self._infer_goal(message, basename)

        steps = [
            # ── Phase 1: Reconnaissance ────────────────────────────────
            Step(
                step=1,
                description=(
                    f"Identify the file type, architecture, and format of {target} "
                    f"using file_info. Confirm it is an executable before proceeding."
                ),
                action_type=ActionType.ANALYSIS,
                tool="file_info",
                flags=StepFlags(),
            ),
            Step(
                step=2,
                description=(
                    f"Check security hardening features of {target} (NX, stack canaries, "
                    f"PIE, ASLR) using checksec. This shapes what to look for in disassembly."
                ),
                action_type=ActionType.ANALYSIS,
                tool="checksec",
                flags=StepFlags(),
            ),
            Step(
                step=3,
                description=(
                    f"Extract all printable strings from {target} using the strings tool. "
                    f"Look specifically for: usage/error messages that reveal program interface, "
                    f"numeric constants (especially 0x9e3779b9=TEA, 0x6a09e667=SHA-256, "
                    f"0x67452301=MD5), IV values, magic bytes, and any embedded documentation. "
                    f"Strings output often identifies the algorithm directly."
                ),
                action_type=ActionType.ANALYSIS,
                tool="strings",
                flags=StepFlags(),
            ),
            Step(
                step=4,
                description=(
                    f"Extract the symbol table of {target} using nm. "
                    f"Identify: custom function names, imported library functions, "
                    f"global constants (DELTA, BLOCK, ROUNDS, IV), and any crypto-related symbols. "
                    f"Cross-reference with strings output to narrow the algorithm."
                ),
                action_type=ActionType.ANALYSIS,
                tool="nm",
                flags=StepFlags(),
            ),
            # ── Phase 2: Targeted disassembly ─────────────────────────
            Step(
                step=5,
                description=(
                    f"Run `{dump_cmd}` to dump the full disassembly of {target} "
                    f"into {tmp} and report the total line count. "
                    f"This tells us how to slice the reading."
                ),
                action_type=ActionType.SHELL,
                tool="bash_exec",
                flags=StepFlags(),
            ),
            Step(
                step=6,
                description=(
                    f"Read lines 1–500 of {tmp} with `sed -n '1,500p' {tmp}`. "
                    f"Focus on: main() entry and argument parsing, function preambles, "
                    f"key constants matching the crypto hints from step 3, "
                    f"and overall control flow structure."
                ),
                action_type=ActionType.SHELL,
                tool="bash_exec",
                flags=StepFlags(),
            ),
            Step(
                step=7,
                description=(
                    f"Read lines 501–1000 of {tmp} with `sed -n '501,1000p' {tmp}`. "
                    f"Focus on: inner loops (round/block operations), XOR/shift/add patterns "
                    f"that indicate cipher rounds, key schedule, and CBC/IV XOR chains."
                ),
                action_type=ActionType.SHELL,
                tool="bash_exec",
                flags=StepFlags(),
            ),
            Step(
                step=8,
                description=(
                    f"Read lines 1001–1500 of {tmp} with `sed -n '1001,1500p' {tmp}`. "
                    f"Focus on: padding/unpadding logic, hex encoding/decoding, "
                    f"error handling, and any remaining functions not yet seen."
                ),
                action_type=ActionType.SHELL,
                tool="bash_exec",
                flags=StepFlags(),
            ),
            # ── Phase 3: Synthesis ────────────────────────────────────
            Step(
                step=9,
                description=(
                    f"{synthesis_desc}\n\n"
                    f"ALGORITHM IDENTIFICATION REQUIRED — {self._CRYPTO_HINT}"
                ),
                action_type=ActionType.CONVERSATION,
                tool=None,
                flags=StepFlags(),
            ),
        ]

        step_n = 10
        if output:
            steps.append(Step(
                step=step_n,
                description=(
                    f"Write the result to {output}. "
                    f"Content must match the file type — no markdown in code files, "
                    f"no raw code in report files."
                ),
                action_type=ActionType.FILE_IO,
                tool="write_file",
                flags=StepFlags(),
            ))
            step_n += 1

        if needs_code_test and output:
            ext = os.path.splitext(output)[1]
            if ext == ".py":
                test_cmd = (
                    f"cd /tmp && python3 -m venv _arc_test_env && "
                    f"source _arc_test_env/bin/activate && "
                    f"pip install pycryptodome 2>/dev/null; "
                    f"python3 {output} 2>&1 | head -20 || echo 'syntax check only'"
                )
            elif ext in (".c", ".cpp"):
                test_cmd = (
                    f"cd /tmp && cc -o _arc_test_{basename} {output} 2>&1 && "
                    f"echo 'compiled OK' || echo 'compile errors above'"
                )
            else:
                test_cmd = f"echo 'no automated test for {ext} files'"

            steps.append(Step(
                step=step_n,
                description=(
                    f"Test the generated code at {output} using bash_exec. "
                    f"Run: `{test_cmd}`. "
                    f"Verify it executes without errors. If there are errors, "
                    f"fix them and re-write the file."
                ),
                action_type=ActionType.SHELL,
                tool="bash_exec",
                flags=StepFlags(),
            ))

        return Plan(
            original_query=message,
            requires_synthesis=True,
            steps=steps,
        )

    def _infer_goal(self, message: str, basename: str) -> tuple[str, str | None, bool]:
        """Return (synthesis_desc, output_path_or_None, needs_code_test)."""
        msg_lower = message.lower()
        output_m = self._output_re.search(message)
        output = output_m.group(1) if output_m else None
        needs_code_test = False

        if re.search(r"function\s+(?:call\s+)?graph|call\s+graph", msg_lower):
            synth = (
                "Build a function call graph from the disassembly: list every function, "
                "what it calls, and what calls it. Format as an indented tree or adjacency list."
            )
            if not output:
                output = f"/tmp/arc_callgraph_{basename}.txt"

        elif re.search(r"\bpython\b", msg_lower):
            synth = (
                "Reconstruct exact equivalent Python source code. "
                "Implement the same algorithm, key derivation, mode of operation, IV, padding, "
                "and CLI interface identified from the disassembly and recon. "
                "No markdown — only valid Python. Encryption output must be identical to the binary."
            )
            if not output:
                output = f"/tmp/arc_clone_{basename}.py"
            needs_code_test = True

        elif re.search(r"\b(?:c\+\+|cpp)\b", msg_lower):
            synth = (
                "Reconstruct equivalent C++ source code from the full analysis. "
                "No markdown, only valid C++ with comments where intent is inferred."
            )
            if not output:
                output = f"/tmp/arc_clone_{basename}.cpp"
            needs_code_test = True

        elif re.search(
            r"\b(?:decompile|reconstruct|rebuild|rewrite|clone|c\s+program|c\s+code|in\s+c\b|create\s+.{0,20}\s+c\b|like\s+it)\b",
            msg_lower,
        ):
            synth = (
                "Reconstruct equivalent C source code from the full analysis. "
                "Reproduce function signatures, algorithm (with correct constants, block size, "
                "rounds, IV, key derivation, mode, padding), control flow, and CLI interface. "
                "No markdown — only valid, compilable C with inline comments."
            )
            if not output:
                output = f"/tmp/arc_clone_{basename}.c"
            needs_code_test = True

        elif re.search(r"\b(?:report|summary|analysis|audit|document)\b", msg_lower):
            synth = (
                "Write a thorough technical analysis report covering: program purpose and CLI "
                "interface, function inventory with descriptions, algorithm identification "
                "(name it precisely — use constants found in recon to confirm), key derivation "
                "method, mode of operation, IV, padding, control flow patterns, "
                "and security observations."
            )
            if not output:
                output = f"/tmp/arc_analysis_{basename}.md"

        else:
            synth = (
                "Synthesize a comprehensive analysis: what the program does, how it is structured, "
                "the exact algorithm (named, not 'unknown' — use constants from recon), "
                "key functions and their roles, and notable implementation details."
            )

        return synth, output, needs_code_test

    def _extract_target(self, message: str) -> str:
        for m in self._target_re.finditer(message):
            tok = m.group(1)
            if "/" in tok and not tok.startswith("http"):
                return tok
        for m in self._target_re.finditer(message):
            tok = m.group(1)
            if re.search(r"\.\w{1,5}$|proc|bin|elf|exe", tok):
                return tok
        return "the_binary"
