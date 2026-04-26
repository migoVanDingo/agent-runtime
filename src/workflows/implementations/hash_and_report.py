import re
from workflows.base import Workflow
from planning.schema import Plan, Step, ActionType, StepFlags


class HashAndReport(Workflow):
    """Matches: 'hash <file>' or 'checksum <file>'"""

    name = "hash-and-report"
    intent = (
        "Use this workflow when the user wants to compute a cryptographic hash or checksum "
        "of a file — e.g. MD5, SHA256, or just 'hash this file'. The output is the hash "
        "value(s) reported back to the user."
    )

    pattern = re.compile(
        r"(?:hash|checksum|md5|sha256?)\s+(\S+)",
        re.IGNORECASE,
    )

    def generate_plan(self, match: re.Match | None, message: str) -> Plan:
        if match is None:
            raise ValueError("HashAndReport requires a regex match to extract the target path")
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
