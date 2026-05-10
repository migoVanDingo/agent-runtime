import re
from planning.schema import Step, ActionType
from skills.base import Skill, SkillContext


class FunctionMap(Skill):
    """Full function inventory and call relationships using radare2."""

    name = "function-map"
    intent = (
        "Use this skill when the user wants to understand a binary's function structure — "
        "what functions exist, how they relate (call graph), and what external libraries are used. "
        "Handles 'list functions', 'call graph', 'what calls what', 'function inventory', "
        "'what does it import', 'show me the structure'."
    )

    pattern = re.compile(
        r"(?:list|show|get|dump)\s+(?:all\s+)?functions?"
        r"|function\s+(?:list|inventory|map|names?)"
        r"|call\s+(?:graph|chain|tree|map)"
        r"|what\s+(?:functions?|calls?)\s+(?:exist|are\s+there|does\s+it\s+have)"
        r"|what\s+calls?\s+what"
        r"|who\s+calls?\s+\w+",
        re.IGNORECASE,
    )

    _target_re = re.compile(r"(?:^|\s)(_?[\w./\-]+(?:/[\w.\-]+)*)\b")

    def expand(self, ctx: SkillContext) -> list[Step]:
        target = self._extract_target(ctx.original_query)
        n = ctx.starting_step_number
        return [
            Step(step=n,
                 description=f"Identify file type of {target} using file_info to confirm it is an executable",
                 action_type=ActionType.ANALYSIS, tool="file_info"),
            Step(step=n + 1,
                 description=f"List all functions in {target} with addresses and sizes using r2_functions",
                 action_type=ActionType.REVERSING, tool="r2_functions"),
            Step(step=n + 2,
                 description=f"Generate call graph of {target} using r2_callgraph",
                 action_type=ActionType.REVERSING, tool="r2_callgraph"),
            Step(step=n + 3,
                 description=f"List all imported symbols of {target} using r2_imports",
                 action_type=ActionType.REVERSING, tool="r2_imports"),
            Step(step=n + 4,
                 description="Summarize the function inventory: key functions, call relationships, and external dependencies",
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
