import re
from planning.schema import Step, ActionType
from skills.base import Skill, SkillContext
from skills.criteria import StructuralCriteria
from runtime.schema import ContinuationDecision


class HashAndReport(Skill):
    """Compute a cryptographic hash or checksum of a file."""

    name = "hash-and-report"
    intent = (
        "Use this skill when the user wants to compute a cryptographic hash or checksum "
        "of a file — e.g. MD5, SHA256, or just 'hash this file'. The output is the hash "
        "value(s) reported back to the user."
    )

    pattern = re.compile(
        r"(?:hash|checksum|md5|sha256?)\s+(\S+)",
        re.IGNORECASE,
    )

    def expand(self, ctx: SkillContext) -> list[Step]:
        m = self.pattern.search(ctx.original_query) if self.pattern else None
        target = m.group(1) if m else "the_file"
        n = ctx.starting_step_number
        return [
            Step(
                step=n,
                description=f"Compute hashes of {target}",
                action_type=ActionType.CRYPTO,
                tool="hash_file",
            ),
        ]

    @property
    def completion_criteria(self):
        return StructuralCriteria(
            tool_name="hash_file",
            predicate=lambda r: bool(r and r.strip()),
            on_met=ContinuationDecision.DONE,
        )
