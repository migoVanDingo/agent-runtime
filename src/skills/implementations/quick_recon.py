import re
from planning.schema import Step, ActionType
from skills.base import Skill, SkillContext


class QuickRecon(Skill):
    """Fast binary triage: file_info → checksec → strings → nm → brief report."""

    name = "quick-recon"
    intent = (
        "Use this skill when the user wants a quick overview of a binary without deep analysis — "
        "file type, architecture, security features, visible strings, and exported symbols. "
        "Handles 'what is this?', 'quick look at', 'check this binary', 'what does it import'."
    )

    pattern = re.compile(
        r"quick\s+(?:look|recon|check|scan|overview|summary)"
        r"|what\s+is\s+this\s+(?:binary|file|executable)"
        r"|brief\s+(?:analysis|overview|summary)"
        r"|triage\s+(?:this|the)",
        re.IGNORECASE,
    )

    _target_re = re.compile(r"(?:^|\s)(_?[\w./\-]+(?:/[\w.\-]+)*)\b")

    def expand(self, ctx: SkillContext) -> list[Step]:
        target = self._extract_target(ctx.original_query)
        n = ctx.starting_step_number
        return [
            Step(step=n,
                 description=f"Identify file type and architecture of {target} using file_info",
                 action_type=ActionType.ANALYSIS, tool="file_info"),
            Step(step=n + 1,
                 description=f"Check security hardening features of {target} (NX, ASLR, stack canary, PIE) using checksec",
                 action_type=ActionType.ANALYSIS, tool="checksec"),
            Step(step=n + 2,
                 description=f"Extract printable strings from {target} — look for usage messages, constants, and algorithm hints",
                 action_type=ActionType.ANALYSIS, tool="strings"),
            Step(step=n + 3,
                 description=f"List exported symbols of {target} using nm — identify function names and imports",
                 action_type=ActionType.ANALYSIS, tool="nm"),
            Step(step=n + 4,
                 description="Summarize findings: what the binary is, what it does, security posture, and notable strings/symbols",
                 action_type=ActionType.CONVERSATION, tool=None),
        ]

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
