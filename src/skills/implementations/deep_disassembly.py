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

        # 0090d — Delegate the heavy reverse-engineering work to the
        # GhidraAnalyst sub-agent. The sub-agent has its own context
        # window so the decompile + intermediate analysis don't bloat
        # the main agent's tokens-per-turn. It returns structured JSON
        # ({algorithm, mode, iv, key_derivation, round_function, …})
        # the synthesizer step can use directly.
        recon_step = Step(
            step=n,
            description=(
                f"Identify the file type, architecture, and format of {target} "
                f"using file_info. Confirm it is an executable before proceeding."
            ),
            action_type=ActionType.ANALYSIS, tool="file_info",
        )
        analyst_step = Step(
            step=n + 1,
            description=(
                f"Delegate full reverse-engineering analysis of {target} to the "
                f"ghidra_analyst sub-agent. Pass this task description: "
                f"\"Analyse {target}. Identify the cryptographic algorithm, mode "
                f"of operation, IV, key derivation, round function. Run dynamic "
                f"verification (compare candidate implementations against the "
                f"binary's output with crafted inputs) to confirm before returning. "
                f"Pay special attention to constants — decompilers render high-bit "
                f"values as negated forms, so try two's complement before declaring "
                f"a constant unknown.\""
            ),
            action_type=ActionType.SUBAGENT,
            tool="subagent_ghidra_analyst",
        )
        synthesis_num = n + 2
        synthesis_steps = [
            Step(
                step=synthesis_num,
                description=(
                    f"{synthesis_desc}\n\n"
                    f"CRITICAL — check the ghidra_analyst result FIRST before doing anything else:\n"
                    f"  - If step {n + 1}'s output starts with 'Error:' (e.g., 'Error: sub-agent ghidra_analyst failed: ...'), "
                    f"the analysis did NOT complete. You MUST NOT proceed with code generation. "
                    f"Instead, STOP and explain the failure to the user — give them the exact error "
                    f"text from the analyst. Do not write a placeholder, dummy, or 'partial' clone. "
                    f"A non-functional clone is worse than no clone because the user may waste time "
                    f"debugging it. Returning the error honestly is the correct action.\n"
                    f"  - If the analyst returned valid JSON with algorithm/mode/key_derivation/etc., "
                    f"use it as the SOURCE OF TRUTH for those fields. Do not invent values not present "
                    f"in the response.\n"
                    f"  - If the analyst's JSON is partial (e.g., algorithm identified but key_derivation "
                    f"is missing), re-invoke subagent_ghidra_analyst with a narrower follow-up question "
                    f"rather than guessing. Never fabricate.\n\n"
                    f"{synthesis_desc}\n\n"
                    f"{self._CRYPTO_HINT}"
                ),
                action_type=ActionType.CONVERSATION, tool=None,
            ),
        ]

        steps = [recon_step, analyst_step] + synthesis_steps

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
