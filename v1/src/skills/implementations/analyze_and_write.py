import re
from planning.schema import Step, ActionType
from skills.base import Skill, SkillContext
from skills.criteria import StructuralCriteria, file_written
from runtime.schema import ContinuationDecision


class AnalyzeAndWrite(Skill):
    """Analyze a file and write findings to an output document."""

    name = "analyze-and-write"
    intent = (
        "Use this skill when the user wants to analyze a file (binary, executable, "
        "or data file) and write the findings to an output document such as a markdown "
        "report, text file, or JSON summary. The target is inspected with file_info and "
        "strings tools, and the results are written to disk."
    )

    pattern = re.compile(
        r"analyze\s+(\S+)"
        r".*?"
        r"(?:write|save|output|put|create)\s+"
        r".*?"
        r"(\S+\.(?:md|txt|json|yml|yaml|csv|log))",
        re.IGNORECASE,
    )

    _EXCLUDE_RE = re.compile(
        r"\b(?:compare|diff|versus|vs\.?|against|between|contrast|"
        r"modify|refactor|rewrite|convert|transform|update|edit)\b",
        re.IGNORECASE,
    )

    _output_re = re.compile(r"(\S+\.(?:md|txt|json|yml|yaml|csv|log))", re.IGNORECASE)
    _target_re = re.compile(r"(?:^|\s)(_?[\w./\-]+(?:/[\w.\-]+)*)\b")

    def expand(self, ctx: SkillContext) -> list[Step]:
        message = ctx.original_query
        target = self._extract_target(message)
        output = self._extract_output(message) or "analysis_output.md"
        n = ctx.starting_step_number
        self._last_output = output
        return [
            Step(
                step=n,
                description=f"Identify the file type, architecture, and basic properties of {target}",
                action_type=ActionType.ANALYSIS,
                tool="file_info",
            ),
            Step(
                step=n + 1,
                description=f"Extract printable strings, version info, and metadata from {target}",
                action_type=ActionType.ANALYSIS,
                tool="strings",
            ),
            Step(
                step=n + 2,
                description=f"Write a structured summary of the analysis findings to {output}",
                action_type=ActionType.FILE_IO,
                tool="write_file",
            ),
        ]

    @property
    def completion_criteria(self):
        output = getattr(self, "_last_output", None)
        if output:
            return StructuralCriteria(
                tool_name="write_file",
                predicate=file_written(output),
                on_met=ContinuationDecision.DONE,
            )
        return None

    def _extract_target(self, message: str) -> str:
        for m in self._target_re.finditer(message):
            tok = m.group(1)
            if "/" in tok and not tok.startswith("http"):
                return tok
        return "the_target"

    def _extract_output(self, message: str) -> str | None:
        m = self._output_re.search(message)
        return m.group(1) if m else None
