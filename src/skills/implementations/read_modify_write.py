import re
from planning.schema import Step, ActionType
from skills.base import Skill, SkillContext
from skills.criteria import StructuralCriteria, file_written
from runtime.schema import ContinuationDecision


class ReadModifyWrite(Skill):
    """Read an existing file, apply transformation, write result to output file."""

    name = "read-modify-write"
    intent = (
        "Use this skill when the user wants to read an existing file, apply some "
        "transformation or modification to its contents (edit, update, convert, reformat, "
        "refactor), and write the result to an output file. Both a source file and a "
        "destination file must be identifiable from the request."
    )

    pattern = re.compile(
        r"read\s+(\S+)"
        r".*?"
        r"(?:modify|update|change|edit|transform|convert)"
        r".*?"
        r"(?:write|save|output|put|create)\s+"
        r".*?"
        r"(\S+\.(?:md|txt|json|yml|yaml|csv|py|js|ts))",
        re.IGNORECASE,
    )

    _output_re = re.compile(r"(\S+\.(?:md|txt|json|yml|yaml|csv|py|js|ts))", re.IGNORECASE)
    _source_re = re.compile(r"read\s+(\S+)", re.IGNORECASE)

    def expand(self, ctx: SkillContext) -> list[Step]:
        message = ctx.original_query
        m = self._source_re.search(message)
        source = m.group(1) if m else "source_file"
        om = self._output_re.search(message)
        output = om.group(1) if om else "output_file"
        self._last_output = output
        n = ctx.starting_step_number
        return [
            Step(
                step=n,
                description=f"Read the contents of {source}",
                action_type=ActionType.FILE_IO,
                tool="read_file",
            ),
            Step(
                step=n + 1,
                description=f"Write the modified content to {output}. Follow the user's instructions for what to modify.",
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
        return StructuralCriteria(
            tool_name="write_file",
            predicate=lambda r: bool(r and r.strip() and "error" not in r.lower()),
            on_met=ContinuationDecision.DONE,
        )
