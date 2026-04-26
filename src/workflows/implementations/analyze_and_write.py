import re
from workflows.base import Workflow
from planning.schema import Plan, Step, ActionType, StepFlags


class AnalyzeAndWrite(Workflow):
    """Matches: 'analyze <target> and write/save to <output>'

    Excludes comparison requests (compare/diff/versus/vs) and cases
    where the target itself is a source code file being compared or
    transformed rather than analyzed as a binary/data artifact.
    """

    name = "analyze-and-write"
    intent = (
        "Use this workflow when the user wants to analyze a file (binary, executable, "
        "or data file) and write the findings to an output document such as a markdown "
        "report, text file, or JSON summary. The target is inspected with file_info and "
        "strings tools, and the results are written to disk."
    )

    pattern = re.compile(
        r"analyze\s+(\S+)"                            # target file/path
        r".*?"                                          # anything in between
        r"(?:write|save|output|put|create)\s+"         # write verb
        r".*?"                                          # optional filler
        r"(\S+\.(?:md|txt|json|yml|yaml|csv|log))",    # output file with extension
        re.IGNORECASE,
    )

    # If any of these appear in the message, don't treat it as a simple analyze-and-write
    _EXCLUDE_RE = re.compile(
        r"\b(?:compare|diff|versus|vs\.?|against|between|contrast|"
        r"modify|refactor|rewrite|convert|transform|update|edit)\b",
        re.IGNORECASE,
    )

    def try_match(self, message: str) -> Plan | None:
        if self._EXCLUDE_RE.search(message):
            return None
        return super().try_match(message)

    def generate_plan(self, match: re.Match | None, message: str) -> Plan:
        if match is None:
            raise ValueError("AnalyzeAndWrite requires a regex match to extract target and output paths")
        target = match.group(1)
        output = match.group(2)
        return Plan(
            original_query=message,
            requires_synthesis=False,
            steps=[
                Step(
                    step=1,
                    description=f"Identify the file type, architecture, and basic properties of {target}",
                    action_type=ActionType.ANALYSIS,
                    tool="file_info",
                    flags=StepFlags(),
                ),
                Step(
                    step=2,
                    description=f"Extract printable strings, version info, and metadata from {target}",
                    action_type=ActionType.ANALYSIS,
                    tool="strings",
                    flags=StepFlags(),
                ),
                Step(
                    step=3,
                    description=f"Write a structured summary of the analysis findings to {output}",
                    action_type=ActionType.FILE_IO,
                    tool="write_file",
                    flags=StepFlags(),
                ),
            ],
        )
