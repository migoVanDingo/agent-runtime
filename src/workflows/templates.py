"""Built-in workflow templates.

Each template matches a common task pattern and generates a Plan
without requiring the LLM planner.
"""

import re
from workflows.base import Workflow
from planning.schema import Plan, Step, ActionType, StepFlags


class AnalyzeAndWrite(Workflow):
    """Matches: 'analyze <target> and write/save to <output>'"""

    name = "analyze-and-write"

    pattern = re.compile(
        r"analyze\s+(\S+)"                            # target file/path
        r".*?"                                          # anything in between
        r"(?:write|save|output|put|create)\s+"         # write verb
        r".*?"                                          # optional filler
        r"(\S+\.(?:md|txt|json|yml|yaml|csv|log))",    # output file with extension
        re.IGNORECASE,
    )

    def generate_plan(self, match: re.Match, message: str) -> Plan:
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


class ReadModifyWrite(Workflow):
    """Matches: 'read <file>, [modify/update/change] ..., write/save to <output>'"""

    name = "read-modify-write"

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

    def generate_plan(self, match: re.Match, message: str) -> Plan:
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


class HashAndReport(Workflow):
    """Matches: 'hash <file>' or 'checksum <file>'"""

    name = "hash-and-report"

    pattern = re.compile(
        r"(?:hash|checksum|md5|sha256?)\s+(\S+)",
        re.IGNORECASE,
    )

    def generate_plan(self, match: re.Match, message: str) -> Plan:
        target = match.group(1)
        return Plan(
            original_query=message,
            requires_synthesis=False,
            steps=[
                Step(
                    step=1,
                    description=f"Compute hashes of {target}",
                    action_type=ActionType.CRYPTO,
                    tool="hash_file",
                    flags=StepFlags(),
                ),
            ],
        )


# All templates in priority order (first match wins)
ALL_WORKFLOWS: list[Workflow] = [
    AnalyzeAndWrite(),
    ReadModifyWrite(),
    HashAndReport(),
]
