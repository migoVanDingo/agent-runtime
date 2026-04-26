import re
from workflows.base import Workflow
from planning.schema import Plan, Step, ActionType, StepFlags


class ReadModifyWrite(Workflow):
    """Matches: 'read <file>, [modify/update/change] ..., write/save to <output>'"""

    name = "read-modify-write"
    intent = (
        "Use this workflow when the user wants to read an existing file, apply some "
        "transformation or modification to its contents (edit, update, convert, reformat, "
        "refactor), and write the result to an output file. Both a source file and a "
        "destination file must be identifiable from the request."
    )

    pattern = re.compile(
        r"read\s+(\S+)"                                # source file
        r".*?"
        r"(?:modify|update|change|edit|transform|convert)"  # modification verb
        r".*?"
        r"(?:write|save|output|put|create)\s+"         # write verb
        r".*?"
        r"(\S+\.(?:md|txt|json|yml|yaml|csv|py|js|ts))",  # output file
        re.IGNORECASE,
    )

    def generate_plan(self, match: re.Match | None, message: str) -> Plan:
        if match is None:
            raise ValueError("ReadModifyWrite requires a regex match to extract source and output paths")
        source = match.group(1)
        output = match.group(2)
        return Plan(
            original_query=message,
            requires_synthesis=False,
            steps=[
                Step(
                    step=1,
                    description=f"Read the contents of {source}",
                    action_type=ActionType.FILE_IO,
                    tool="read_file",
                    flags=StepFlags(),
                ),
                Step(
                    step=2,
                    description=f"Write the modified content to {output}. Follow the user's instructions for what to modify.",
                    action_type=ActionType.FILE_IO,
                    tool="write_file",
                    flags=StepFlags(),
                ),
            ],
        )
