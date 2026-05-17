import re
from planning.schema import Step, ActionType
from skills.base import Skill, SkillContext


class SolveCrackme(Skill):
    """CTF-style: find the input that satisfies the binary using angr symbolic execution."""

    name = "solve-crackme"
    intent = (
        "Use this skill when the user wants to find an input (password, key, flag) that "
        "satisfies a binary — CTF crackmes, license checks, checksum validation. "
        "Handles 'solve the crackme', 'find the password', 'find input that prints success', "
        "'what key does it accept', 'crack the binary'."
    )

    pattern = re.compile(
        r"solve\s+(?:the\s+)?(?:crackme|passphrase|password|challenge)"
        r"|find\s+(?:the\s+)?(?:password|key|flag|input|solution)"
        r"|crack\s+(?:the\s+)?(?:binary|program|executable)"
        r"|what\s+(?:password|key|input)\s+(?:does\s+it\s+accept|is\s+correct)"
        r"|find\s+input\s+that\s+(?:makes|causes|triggers|satisfies)"
        r"|angr\s+solve",
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
                 description=f"Extract strings from {target} — find success/failure messages that can serve as find/avoid targets",
                 action_type=ActionType.ANALYSIS, tool="strings"),
            Step(step=n + 2,
                 description=f"List all functions in {target} using r2_functions — identify main, check, validate, or win functions",
                 action_type=ActionType.REVERSING, tool="r2_functions"),
            Step(step=n + 3,
                 description=(
                     f"Based on strings and function names found, identify the success address "
                     f"and failure addresses. Then use angr_solve to find the input (stdin or argv) "
                     f"that reaches the success address. Set find=<success_addr> and avoid=<failure_addrs>."
                 ),
                 action_type=ActionType.SYMBOLIC, tool="angr_solve"),
            Step(step=n + 4,
                 description="Report the solved input and verify it makes sense given the program's logic",
                 action_type=ActionType.CONVERSATION, tool=None),
        ]

    def _extract_target(self, message: str) -> str:
        for m in self._target_re.finditer(message):
            tok = m.group(1)
            if "/" in tok and not tok.startswith("http"):
                return tok
        for m in self._target_re.finditer(message):
            tok = m.group(1)
            if re.search(r"\.\w{1,5}$|proc|bin|elf|exe|crackme", tok):
                return tok
        return "the_binary"
