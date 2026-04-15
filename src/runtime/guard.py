import re
from enum import Enum
from logger import get_logger

logger = get_logger(__name__)


class GuardDecision(str, Enum):
    ALLOW    = "allow"
    BLOCK    = "block"
    ESCALATE = "escalate"


# ── Dangerous shell patterns ────────────────────────────────────────

_DANGEROUS_COMMANDS = re.compile(
    r"(?:^|\s|;|&&|\|\||`|\$\()"  # preceded by start, whitespace, or shell operator
    r"(?:sudo\s+)?"               # optional sudo prefix
    r"("
    r"rm\s+-[rR]f?\b|rm\s+-f?[rR]\b"  # rm -rf, rm -fr, rm -r, rm -f
    r"|rmdir\s"                         # rmdir
    r"|dd\s"                            # dd (disk destroyer)
    r"|mkfs[.\s]"                       # mkfs
    r"|format\s"                        # format
    r"|kill\s+-9\b"                     # kill -9
    r"|killall\s"                       # killall
    r"|pkill\s"                         # pkill
    r"|shutdown\b"                      # shutdown
    r"|reboot\b"                        # reboot
    r"|halt\b"                          # halt
    r"|init\s+0\b"                      # init 0
    r"|chmod\s+777\b"                   # chmod 777
    r"|chmod\s+-R\b"                    # recursive chmod
    r"|chown\s+-R\b"                    # recursive chown
    r"|>\s*/dev/"                        # write to /dev/
    r"|curl\s.*\|\s*(?:ba)?sh"          # curl | sh (pipe to shell)
    r"|wget\s.*\|\s*(?:ba)?sh"          # wget | sh
    r")",
    re.IGNORECASE,
)

_SUDO_PATTERN = re.compile(r"(?:^|\s|;|&&|\|\|)sudo\s", re.IGNORECASE)

# ── Sensitive paths ─────────────────────────────────────────────────

_SENSITIVE_PATHS = re.compile(
    r"(?:^|/)"
    r"(?:etc|usr|var|boot|sys|proc|dev"
    r"|\.ssh|\.gnupg|\.aws|\.config"
    r"|\.env|\.git/config|\.gitconfig"
    r"|passwd|shadow|sudoers"
    r")(?:/|$)",
    re.IGNORECASE,
)


class ActionGuard:
    """Pre-execution safety gate. Code-only, no LLM calls.

    Inspects tool calls before execution and returns ALLOW, BLOCK, or ESCALATE.
    """

    def check_step(self, description: str, action_type: str) -> GuardDecision:
        """Pre-flight check on a step description before it starts."""
        lowered = description.lower()

        if any(word in lowered for word in ("delete all", "remove all", "wipe", "destroy", "purge")):
            logger.info(f"  guard: step description contains destructive language — ESCALATE")
            return GuardDecision.ESCALATE

        return GuardDecision.ALLOW

    def check_tool_call(self, tool_name: str, tool_input: dict) -> tuple[GuardDecision, str]:
        """Check a specific tool invocation before execution.

        Returns (decision, reason). Reason is empty for ALLOW.
        """

        # ── bash_exec: most dangerous surface ──
        if tool_name == "bash_exec":
            command = tool_input.get("command", "")
            return self._check_shell_command(command)

        # ── delete_file: always escalate ──
        if tool_name == "delete_file":
            path = tool_input.get("path", "?")
            return GuardDecision.ESCALATE, f"delete_file on '{path}'"

        # ── write_file: check for sensitive paths ──
        if tool_name == "write_file":
            path = tool_input.get("path", "")
            if _SENSITIVE_PATHS.search(path):
                return GuardDecision.ESCALATE, f"write_file to sensitive path '{path}'"

        # ── move_file: check for sensitive destinations ──
        if tool_name == "move_file":
            dest = tool_input.get("destination", tool_input.get("dest", ""))
            source = tool_input.get("source", tool_input.get("path", ""))
            if _SENSITIVE_PATHS.search(dest) or _SENSITIVE_PATHS.search(source):
                return GuardDecision.ESCALATE, f"move_file involving sensitive path"

        # ── strace/ltrace: can attach to arbitrary processes ──
        if tool_name in ("strace", "ltrace"):
            pid = tool_input.get("pid")
            if pid is not None:
                return GuardDecision.ESCALATE, f"{tool_name} attaching to pid {pid}"

        return GuardDecision.ALLOW, ""

    def _check_shell_command(self, command: str) -> tuple[GuardDecision, str]:
        """Inspect a shell command string for dangerous patterns."""

        # Dangerous command patterns → BLOCK
        match = _DANGEROUS_COMMANDS.search(command)
        if match:
            return GuardDecision.BLOCK, f"dangerous command pattern: '{match.group(0).strip()}'"

        # Sudo → ESCALATE (not auto-block — might be intentional)
        if _SUDO_PATTERN.search(command):
            return GuardDecision.ESCALATE, f"command uses sudo"

        # Sensitive path targets → ESCALATE
        # Only check if command is writing/modifying (not reading)
        write_indicators = re.compile(r"\b(tee|mv|cp|install|sed\s+-i|chmod|chown)\b")
        if write_indicators.search(command) and _SENSITIVE_PATHS.search(command):
            return GuardDecision.ESCALATE, f"modifying sensitive path"

        return GuardDecision.ALLOW, ""
