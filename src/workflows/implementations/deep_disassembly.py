import os
import platform
import re
from workflows.base import Workflow
from planning.schema import Plan, Step, ActionType, StepFlags


class DeepDisassembly(Workflow):
    """Matches any deep disassembly / decompile / reverse-engineering request.

    Handles large output by dumping to a temp file and reading in 500-line
    chunks. The synthesis goal and output file adapt to whatever the user
    actually asked for: source code reconstruction (any language), analysis
    report, function call graph, security audit, etc.

    If no output file is mentioned the workflow ends at the synthesis step
    (no write_file), leaving the result in the conversation.
    """

    name = "deep-disassembly"
    intent = (
        "Use this workflow when the user wants to deeply analyze, disassemble, decompile, "
        "or reverse-engineer a binary or executable — including requests to reconstruct or "
        "rewrite it in a programming language (C, Python, Rust, etc.), understand how it "
        "works internally, generate a function call graph, or produce a detailed technical "
        "analysis or security audit. This handles large disassembly output by chunking it "
        "and synthesizing results across multiple passes."
    )

    pattern = re.compile(
        r"(?:decompile|disassemble|objdump|reconstruct|reverse[\s-]?engineer)"
        r"|rebuild\s+(?:it\s+)?in\s+(?:c|python|rust|go|java|swift)\b"
        r"|function\s+(?:call\s+)?graph",
        re.IGNORECASE,
    )

    # Matches any explicit output file with a code or doc extension
    _output_re = re.compile(
        r"(\S+\.(?:c|h|cpp|py|rs|go|java|swift|js|ts|md|txt|json))\b"
    )
    _target_re = re.compile(r"(?:^|\s)(_?[\w./\-]+(?:/[\w.\-]+)*)\b")

    def generate_plan(self, match: re.Match | None, message: str) -> Plan:
        # match is not used — target and goal are extracted from the message directly.
        # This workflow can be invoked with match=None by the classifier hint path.
        target = self._extract_target(message)
        basename = os.path.basename(target).replace(".", "_")
        tmp = f"/tmp/arc_disasm_{basename}.asm"

        if platform.system() == "Darwin":
            dump_cmd = f"otool -tv {target} > {tmp} 2>&1 && wc -l {tmp}"
        else:
            dump_cmd = f"objdump -d {target} > {tmp} 2>&1 && wc -l {tmp}"

        synthesis_desc, output = self._infer_goal(message, basename)

        steps = [
            Step(
                step=1,
                description=(
                    f"Run `{dump_cmd}` to dump the full disassembly of {target} "
                    f"into {tmp} and report the total line count."
                ),
                action_type=ActionType.SHELL,
                tool="bash_exec",
                flags=StepFlags(),
            ),
            Step(
                step=2,
                description=(
                    f"Read lines 1–500 of {tmp} with `sed -n '1,500p' {tmp}`. "
                    f"Identify function preambles, control flow, key constants, and data structures."
                ),
                action_type=ActionType.SHELL,
                tool="bash_exec",
                flags=StepFlags(),
            ),
            Step(
                step=3,
                description=(
                    f"Read lines 501–1000 of {tmp} with `sed -n '501,1000p' {tmp}`. "
                    f"Note loops, branches, function calls, and patterns from the previous section."
                ),
                action_type=ActionType.SHELL,
                tool="bash_exec",
                flags=StepFlags(),
            ),
            Step(
                step=4,
                description=(
                    f"Read lines 1001–1500 of {tmp} with `sed -n '1001,1500p' {tmp}`. "
                    f"Capture any remaining logic, error handling, and exit paths."
                ),
                action_type=ActionType.SHELL,
                tool="bash_exec",
                flags=StepFlags(),
            ),
            Step(
                step=5,
                description=synthesis_desc,
                action_type=ActionType.CONVERSATION,
                tool=None,
                flags=StepFlags(),
            ),
        ]

        if output:
            steps.append(Step(
                step=6,
                description=(
                    f"Write the result to {output}. "
                    f"Content must match the file type — no markdown in code files, "
                    f"no raw code in report files. Do not write to any other path."
                ),
                action_type=ActionType.FILE_IO,
                tool="write_file",
                flags=StepFlags(),
            ))

        return Plan(
            original_query=message,
            requires_synthesis=True,
            steps=steps,
        )

    def _infer_goal(self, message: str, basename: str) -> tuple[str, str | None]:
        """Return (synthesis_step_description, output_path_or_None)."""
        msg_lower = message.lower()

        # Detect explicit output file first
        output_m = self._output_re.search(message)
        output = output_m.group(1) if output_m else None

        # Detect the type of output requested
        if re.search(r"function\s+(?:call\s+)?graph|call\s+graph", msg_lower):
            synth = (
                "Build a function call graph from the disassembly: list every function, "
                "what it calls, and what calls it. Format as an indented tree or adjacency list."
            )
            if not output:
                output = f"/tmp/arc_callgraph_{basename}.txt"

        elif re.search(r"\bpython\b", msg_lower):
            synth = (
                "Reconstruct equivalent Python source code from the disassembly analysis. "
                "Translate logic, control flow, and data structures into idiomatic Python. "
                "Use comments where assembly intent must be inferred. No markdown, only valid Python."
            )
            if not output:
                output = f"/tmp/arc_reconstructed_{basename}.py"

        elif re.search(r"\b(?:c\+\+|cpp)\b", msg_lower):
            synth = (
                "Reconstruct equivalent C++ source code from the disassembly analysis. "
                "No markdown, only valid C++ source with comments where intent is inferred."
            )
            if not output:
                output = f"/tmp/arc_reconstructed_{basename}.cpp"

        elif re.search(r"\b(?:decompile|reconstruct|rebuild|rewrite|c\s+program|c\s+code|in\s+c|create\s+.{0,20}\s+c\b|like\s+it)\b", msg_lower):
            synth = (
                "Reconstruct equivalent C source code from the disassembly analysis. "
                "Reproduce function signatures, control flow, data types, and constants. "
                "Write idiomatic, compilable C. No markdown — only valid C source with "
                "inline comments where intent is inferred."
            )
            if not output:
                output = f"/tmp/arc_reconstructed_{basename}.c"

        elif re.search(r"\b(?:report|summary|analysis|audit|document)\b", msg_lower):
            synth = (
                "Write a thorough technical analysis report covering: program purpose, "
                "function inventory, control flow patterns, algorithms and data structures "
                "identified, interesting constants or strings, and any security-relevant observations. "
                "Structure it with clear sections."
            )
            if not output:
                output = f"/tmp/arc_analysis_{basename}.md"

        else:
            # Generic deep analysis — no forced output file
            synth = (
                "Synthesize a comprehensive analysis of the binary from all three disassembly "
                "sections: describe what the program does, how it is structured, its key functions, "
                "control flow patterns, and any notable implementation details."
            )
            # Only write if the user mentioned an explicit file
            # output stays None if not specified

        return synth, output

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
