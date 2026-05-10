import re
from planning.schema import Step, ActionType
from skills.base import Skill, SkillContext

_DANGEROUS_FUNS = "strcpy, gets, sprintf, strcat, scanf, memcpy, read, write, system, popen"


class AuditBinary(Skill):
    """Security audit: recon → dangerous imports → xrefs → angr sink reachability → report."""

    name = "audit-binary"
    intent = (
        "Use this skill when the user wants a security audit of a binary — "
        "finding vulnerabilities, dangerous function calls, buffer overflows, attack surface. "
        "Handles 'security audit', 'find vulnerabilities', 'find buffer overflows', "
        "'what dangerous functions', 'attack surface', 'security analysis'."
    )

    pattern = re.compile(
        r"security\s+audit"
        r"|find\s+(?:vulnerabilit(?:y|ies)|bugs?|buffer\s+overflow|overflows?)"
        r"|(?:attack|exploit)\s+surface"
        r"|dangerous\s+(?:functions?|calls?)"
        r"|vulnerability\s+(?:scan|analysis|report)"
        r"|audit\s+(?:the\s+)?(?:binary|executable|program)",
        re.IGNORECASE,
    )

    _target_re = re.compile(r"(?:^|\s)(_?[\w./\-]+(?:/[\w.\-]+)*)\b")
    _output_re = re.compile(r"(\S+\.(?:md|txt|json))\b")

    def expand(self, ctx: SkillContext) -> list[Step]:
        message = ctx.original_query
        target = self._extract_target(message)
        output_m = self._output_re.search(message)
        output = output_m.group(1) if output_m else None
        n = ctx.starting_step_number

        return [
            Step(step=n,
                 description=f"Identify file type and architecture of {target} using file_info",
                 action_type=ActionType.ANALYSIS, tool="file_info"),
            Step(step=n + 1,
                 description=f"Check security hardening of {target}: NX, ASLR, stack canaries, PIE, RELRO using checksec",
                 action_type=ActionType.ANALYSIS, tool="checksec"),
            Step(step=n + 2,
                 description=f"List all imports of {target} using r2_imports — flag dangerous functions: {_DANGEROUS_FUNS}",
                 action_type=ActionType.REVERSING, tool="r2_imports"),
            Step(step=n + 3,
                 description=(
                     f"For each dangerous function found in the previous step, use r2_xrefs to find "
                     f"what functions call it. Focus on: gets, strcpy, sprintf, system."
                 ),
                 action_type=ActionType.REVERSING, tool="r2_xrefs"),
            Step(step=n + 4,
                 description=(
                     f"For the top 1-2 dangerous call sites found, use angr_reachable to check if "
                     f"those sink addresses are reachable from the program entry point."
                 ),
                 action_type=ActionType.SYMBOLIC, tool="angr_reachable"),
            Step(step=n + 5,
                 description=(
                     f"Write a security audit report covering: "
                     f"(1) security hardening posture, "
                     f"(2) dangerous imports and their call sites, "
                     f"(3) reachability of vulnerable sinks, "
                     f"(4) overall risk assessment and recommended mitigations."
                     + (f" Write to {output}." if output else "")
                 ),
                 action_type=ActionType.FILE_IO if output else ActionType.CONVERSATION,
                 tool="write_file" if output else None),
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
