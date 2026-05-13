"""Deep binary analysis skill.

Platform detection and capability checks (Ghidra availability,
ContainerSession) are intentionally absent — skills declare WHAT to do;
infrastructure decides HOW based on runtime capabilities.
"""
import os
import re
from planning.schema import Step, ActionType
from skills.base import Skill, SkillContext
from skills.criteria import LLMJudgedCriteria
from runtime.schema import ContinuationDecision


class DeepDisassembly(Skill):
    """Deep binary analysis: recon → Ghidra decompile → algorithm ID → output.

    Heavy tool outputs (Ghidra decompile, radare2 disasm, etc.) are automatically
    paged to _analysis/<binary>/ so they survive context compaction and avoid TPM
    rate-limit errors. Check the analysis manifest in the system prompt before
    re-running any heavy tool — the artifact may already exist.
    """

    name = "deep-disassembly"
    intent = (
        "Use this skill when the user wants to deeply analyze, disassemble, decompile, "
        "or reverse-engineer a binary or executable — including requests to reconstruct or "
        "rewrite it in a programming language (C, Python, Rust, etc.), understand how it "
        "works internally, generate a function call graph, produce a detailed technical "
        "analysis or security audit, or clone/replicate its behavior. "
        "This skill does reconnaissance first (strings, nm, checksec) to identify "
        "algorithms and key constants before decompiling. "
        )

    pattern = re.compile(
        r"(?:decompile|disassemble|objdump|reconstruct|reverse[\s-]?engineer)"
        r"|rebuild\s+(?:it\s+)?in\s+(?:c|python|rust|go|java|swift)\b"
        r"|function\s+(?:call\s+)?graph"
        r"|analyze\s+(?:the\s+)?(?:binary|executable|program|proc\b)"
        r"|clone(?:\s+the)?\s+binary",
        re.IGNORECASE,
    )

    _output_re = re.compile(r"(\S+\.(?:c|h|cpp|py|rs|go|java|swift|js|ts|md|txt|json))\b")
    _target_re = re.compile(r"(?:^|\s)(_?[\w./\-]+(?:/[\w.\-]+)*)\b")

    _CRYPTO_HINT = (
        "If any of these constants appear in strings or decompilation, identify the algorithm "
        "immediately — do not say the algorithm is unknown:\n"
        "  0x9e3779b9 or 2654435769 → TEA / XTEA / XXTEA (block size 8 bytes, 32 rounds)\n"
        "  0x61c88647 → TEA (negative delta)\n"
        "  0x6a09e667 → SHA-256\n"
        "  0x67452301 → MD5\n"
        "Report the block size, key derivation method, mode of operation (ECB, CBC, CTR), "
        "IV (if any), and padding scheme."
    )

    @property
    def completion_criteria(self):
        return LLMJudgedCriteria(
            prompt=(
                "Did the agent produce a complete result for the requested binary analysis? "
                "A result is complete when ALL of the following that were actually requested are done:\n"
                "- If analysis/audit was requested: algorithm identified (named, not 'unknown'), "
                "  security findings summarized, function inventory produced.\n"
                "- If source reconstruction was requested: working C/Python/etc. implementation written.\n"
                "- If a verification step was requested: oracle test run.\n"
                "Do NOT require source code reconstruction if the user only asked for analysis or audit. "
                "Do NOT require verification if no oracle was provided. "
                "Judge based on what was actually requested, not a fixed checklist."
            ),
            on_met=ContinuationDecision.SYNTHESIZE,
        )

    def expand(self, ctx: SkillContext) -> list[Step]:
        message = ctx.original_query
        target = self._extract_target(message)
        basename = os.path.basename(target).replace(".", "_")
        output = self._extract_output(message)
        synthesis_desc = self._infer_synthesis(message, basename, output)
        n = ctx.starting_step_number

        recon_steps = [
            Step(step=n,
                 description=(
                     f"Identify the file type, architecture, and format of {target} "
                     f"using file_info. Confirm it is an executable before proceeding."
                 ),
                 action_type=ActionType.ANALYSIS, tool="file_info"),
            Step(step=n + 1,
                 description=f"Check security hardening features of {target} (NX, stack canaries, PIE, ASLR) using checksec.",
                 action_type=ActionType.ANALYSIS, tool="checksec"),
            Step(step=n + 2,
                 description=(
                     f"Extract all printable strings from {target}. Look for: usage/error messages, "
                     f"numeric constants (0x9e3779b9=TEA, 0x6a09e667=SHA-256, 0x67452301=MD5), "
                     f"IV values, magic bytes."
                 ),
                 action_type=ActionType.ANALYSIS, tool="strings"),
            Step(step=n + 3,
                 description=(
                     f"Extract the symbol table of {target} using nm. "
                     f"Identify: custom function names, global constants (DELTA, BLOCK, ROUNDS, IV), "
                     f"crypto-related symbols."
                 ),
                 action_type=ActionType.ANALYSIS, tool="nm"),
        ]

        ghidra_steps = [
            Step(step=n + 4,
                 description=(
                     f"Run Ghidra analysis on {target} using ghidra_analyze to build the project cache."
                 ),
                 action_type=ActionType.REVERSING, tool="ghidra_analyze"),
            Step(step=n + 5,
                 description=(
                     f"List all functions in {target} using ghidra_functions. "
                     f"Identify user-defined functions and any crypto-named symbols."
                 ),
                 action_type=ActionType.REVERSING, tool="ghidra_functions"),
            Step(step=n + 6,
                 description=(
                     f"Decompile all functions of {target} to C pseudocode using ghidra_decompile. "
                     f"Focus on: main(), encrypt/decrypt functions, key derivation, padding logic, "
                     f"mode of operation (ECB, CBC, CTR), IV handling, and round count."
                 ),
                 action_type=ActionType.REVERSING, tool="ghidra_decompile"),
            Step(step=n + 7,
                 description=(
                     f"Find magic constants and data references in {target} using ghidra_find_constants. "
                     f"Confirm any crypto constants identified in strings/nm."
                 ),
                 action_type=ActionType.REVERSING, tool="ghidra_find_constants"),
        ]

        read_num = n + 8
        synthesis_num = n + 9
        from session_paths import virtual_analysis_path
        decompile_path = virtual_analysis_path(target, "ghidra_decompile.txt")
        read_step = Step(
            step=read_num,
            description=(
                f"Read {decompile_path} using read_file. "
                f"The decompile output must be in context for synthesis — do not skip."
            ),
            action_type=ActionType.ANALYSIS,
            tool="read_file",
        )
        synthesis_steps = [
            read_step,
            Step(step=synthesis_num,
                 description=(
                     f"{synthesis_desc}\n\n"
                     f"ALGORITHM IDENTIFICATION REQUIRED — {self._CRYPTO_HINT}"
                 ),
                 action_type=ActionType.CONVERSATION, tool=None),
        ]

        steps = recon_steps + ghidra_steps + synthesis_steps

        if output:
            steps.append(Step(
                step=synthesis_num + 1,
                description=(
                    f"Write the result to {output}. "
                    f"Content must match the file type — no markdown in code files."
                ),
                action_type=ActionType.FILE_IO,
                tool="write_file",
            ))

        return steps

    def _infer_synthesis(self, message: str, basename: str, output: str | None) -> str:
        msg_lower = message.lower()
        if re.search(r"function\s+(?:call\s+)?graph|call\s+graph", msg_lower):
            return (
                "Build a function call graph from the decompilation: list every function, "
                "what it calls, and what calls it. Format as an indented tree or adjacency list."
            )
        if re.search(r"\bpython\b", msg_lower):
            return (
                "Reconstruct exact equivalent Python source code. "
                "Implement the same algorithm, key derivation, mode, IV, padding, and CLI interface. "
                "No markdown — only valid Python."
            )
        if re.search(r"\b(?:c\+\+|cpp)\b", msg_lower):
            return (
                "Reconstruct equivalent C++ source code. No markdown, only valid C++ with comments."
            )
        if re.search(
            r"\b(?:decompile|reconstruct|rebuild|rewrite|clone|c\s+program|c\s+code|in\s+c\b)\b",
            msg_lower,
        ):
            return (
                "Reconstruct equivalent C source code from the full analysis. "
                "Before writing, confirm from the decompilation: key derivation byte-level operation, "
                "mode of operation (look for XOR with previous block = CBC), IV bytes, and padding. "
                "No markdown — only valid compilable C."
            )
        if re.search(r"\b(?:report|summary|analysis|audit|document)\b", msg_lower):
            return (
                "Write a thorough technical analysis report covering: program purpose, function "
                "inventory, algorithm identification, key derivation, mode, IV, padding, and security observations."
            )
        return (
            "Synthesize a comprehensive analysis: what the program does, how it is structured, "
            "the exact algorithm (named, not 'unknown'), key functions and their roles."
        )

    _OUTPUT_EXT_RE = re.compile(r'\.(c|cpp|py|rs|go|java|js|ts|md|txt|json|html)$', re.IGNORECASE)
    _OUTPUT_PREFIXES = ("_analysis/", "_sessions/", "_rag/", "_store/", "_logs/", "_tests/")
    # Common words users say when describing a binary — not actual filenames
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
