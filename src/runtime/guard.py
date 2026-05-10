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
    r"|>\s*/dev/(?!null\b|stderr\b|stdout\b)"  # write to /dev/ (allow /dev/null, /dev/stderr, /dev/stdout)
    r"|curl\s.*\|\s*(?:ba)?sh"          # curl | sh (pipe to shell)
    r"|wget\s.*\|\s*(?:ba)?sh"          # wget | sh
    r")",
    re.IGNORECASE,
)

_SUDO_PATTERN = re.compile(r"(?:^|\s|;|&&|\|\|)sudo\s", re.IGNORECASE)

# ── Package manager / installer patterns → ESCALATE ────────────────

_PACKAGE_MANAGERS = re.compile(
    r"(?:^|\s|;|&&|\|\||`|\$\()"
    r"("
    r"brew\s+(?:install|uninstall|remove|upgrade)\b"
    r"|pip3?\s+(?:install|uninstall)\b"
    r"|apt(?:-get)?\s+(?:install|remove|purge|upgrade)\b"
    r"|dnf\s+(?:install|remove|upgrade)\b"
    r"|yum\s+(?:install|remove|upgrade)\b"
    r"|npm\s+(?:install\s+-g|uninstall\s+-g)\b"
    r"|yarn\s+global\s+(?:add|remove)\b"
    r"|gem\s+(?:install|uninstall)\b"
    r"|cargo\s+install\b"
    r"|go\s+install\b"
    r")",
    re.IGNORECASE,
)

# ── Network / remote-access commands → ESCALATE ────────────────────
# curl and wget without pipe-to-shell are not dangerous enough to BLOCK
# but any outbound connection should require user approval. scp/ssh/rsync
# can exfiltrate files; nc/ftp/sftp open raw network channels.

_NETWORK_COMMANDS = re.compile(
    r"(?:^|\s|;|&&|\|\||`|\$\()"
    r"("
    r"curl\s"                           # any curl invocation
    r"|wget\s"                          # any wget (pipe-to-shell already BLOCKed above)
    r"|scp\s"                           # secure copy (file exfiltration)
    r"|ssh\s"                           # remote shell / tunneling
    r"|sftp\s"                          # SSH file transfer
    r"|rsync\s"                         # remote sync (can exfiltrate)
    r"|ftp\s"                           # plain FTP
    r"|nc\s|ncat\s|netcat\s"            # netcat (raw network pipe)
    r"|socat\s"                         # socat (like netcat but more powerful)
    r")",
    re.IGNORECASE,
)

# ── Arbitrary code execution patterns → ESCALATE ───────────────────

_CODE_EXECUTION = re.compile(
    r"(?:^|\s|;|&&|\|\||`|\$\()"
    r"("
    r"python[23]?\s+-c\b"
    r"|ruby\s+-e\b"
    r"|perl\s+-e\b"
    r"|node\s+-e\b"
    r")",
    re.IGNORECASE,
)

# ── Script file execution → ESCALATE ───────────────────────────────
# Matches: interpreter followed by a file path argument (contains . or /)
# Distinct from inline code (-c flag) which _CODE_EXECUTION already catches.

_SCRIPT_EXECUTION = re.compile(
    r"(?:^|\s|;|&&|\|\||`|\$\()"
    r"(?:python[23]?|bash|sh|zsh|node|ruby|perl)"
    r"\s+"
    r"(?!-)"           # not a flag
    r"[\w./\-]+"       # file path token (contains word chars, dots, slashes, hyphens)
    r"(?:\.py|\.sh|\.js|\.rb|\.pl|\.ts|[/][\w./\-]*)",  # must look like a file
    re.IGNORECASE,
)

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


def _approval_key(tool_name: str, tool_input: dict) -> str | None:
    """Derive a cache key for an escalation approval.

    Returns a string key that covers all equivalent invocations the user
    would consider the same approval, or None if not cacheable.
    """
    if tool_name == "bash_exec":
        command = tool_input.get("command", "")
        # Key on the script path for interpreter+file commands
        m = _SCRIPT_EXECUTION.search(command)
        if m:
            # Extract the file token from the match
            tokens = m.group(0).strip().split()
            if len(tokens) >= 2:
                return f"bash_exec:script:{tokens[-1]}"
        # For other escalated shell patterns, key on full command
        return f"bash_exec:{command}"
    if tool_name == "delete_file":
        return f"delete_file:{tool_input.get('path', '')}"
    if tool_name == "delete_directory":
        return f"delete_directory:{tool_input.get('path', '')}"
    if tool_name in ("strace", "ltrace"):
        return f"{tool_name}:pid:{tool_input.get('pid', '')}"
    if tool_name == "write_file":
        return f"write_file:{tool_input.get('path', '')}"
    if tool_name == "move_file":
        return f"move_file:{tool_input.get('source', '')}:{tool_input.get('destination', '')}"
    if tool_name == "http_request":
        method = tool_input.get("method", "GET").upper()
        url = tool_input.get("url", "")
        return f"http_request:{method}:{url}"
    if tool_name in ("read_url", "extract_html"):
        url = tool_input.get("url", tool_input.get("source", ""))
        return f"{tool_name}:{url}"
    if tool_name == "dataframe_query":
        return f"dataframe_query:{tool_input.get('expression', '')}"
    if tool_name == "expel_artifact":
        return f"expel_artifact:{tool_input.get('key', '')}"
    # ghidra_* and angr_* — key on tool name + binary path so one approval covers all calls
    if tool_name.startswith("ghidra_") or tool_name.startswith("angr_"):
        binary = tool_input.get("path", tool_input.get("binary", ""))
        return f"{tool_name}:{binary}"
    return None


class ActionGuard:
    """Pre-execution safety gate. Code-only, no LLM calls.

    Inspects tool calls before execution and returns ALLOW, BLOCK, or ESCALATE.
    Approved escalations are cached per session so the user is not asked
    repeatedly for the same operation.
    """

    def __init__(self):
        self._approved: set[str] = set()

    def record_approval(self, tool_name: str, tool_input: dict) -> None:
        """Record that the user approved this tool call. Suppresses future escalations."""
        key = _approval_key(tool_name, tool_input)
        if key:
            self._approved.add(key)
            logger.info(f"  guard: approval cached — {key}")

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
        Cached approvals short-circuit to ALLOW with a log note.
        """
        # ── Approval cache: user already approved this pattern ──
        key = _approval_key(tool_name, tool_input)
        if key and key in self._approved:
            logger.info(f"  guard: ✓ approved (cached): {key}")
            return GuardDecision.ALLOW, ""

        # ── bash_exec: most dangerous surface ──
        if tool_name == "bash_exec":
            command = tool_input.get("command", "")
            return self._check_shell_command(command)

        # ── delete_file / delete_directory: always escalate ──
        if tool_name == "delete_file":
            path = tool_input.get("path", "?")
            return GuardDecision.ESCALATE, f"delete_file on '{path}'"
        if tool_name == "delete_directory":
            path = tool_input.get("path", "?")
            return GuardDecision.ESCALATE, f"delete_directory on '{path}' (recursive)"

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

        # ── http_request: all outbound HTTP requires approval ──
        if tool_name == "http_request":
            method = tool_input.get("method", "GET").upper()
            url = tool_input.get("url", "?")
            return GuardDecision.ESCALATE, f"outbound HTTP {method} → {url}"

        # ── read_url / extract_html: read-only but fetch external content ──
        if tool_name in ("read_url", "extract_html"):
            url = tool_input.get("url", tool_input.get("source", "?"))
            return GuardDecision.ESCALATE, f"{tool_name} fetching external content from '{url}'"

        # ── dataframe_query: evaluates user-provided expression ──
        if tool_name == "dataframe_query":
            expression = tool_input.get("expression", "")
            if len(expression) > 120:
                expression = expression[:117] + "..."
            return GuardDecision.ESCALATE, f"dataframe_query expression eval: '{expression}'"

        # ── expel_artifact: destructive delete operation ──
        if tool_name == "expel_artifact":
            key = tool_input.get("key", "?")
            return GuardDecision.ESCALATE, f"expel_artifact deleting '{key}'"

        # ── ghidra_*: runs analyzeHeadless on the host outside the sandbox ──
        if tool_name.startswith("ghidra_"):
            binary = tool_input.get("path", tool_input.get("binary", "?"))
            return GuardDecision.ESCALATE, f"host execution: {tool_name} on '{binary}'"

        # ── angr_*: symbolic execution runs the binary on the host ──
        if tool_name.startswith("angr_"):
            binary = tool_input.get("path", tool_input.get("binary", "?"))
            return GuardDecision.ESCALATE, f"host symbolic execution: {tool_name} on '{binary}'"

        # ── lldb_*: attaches LLDB to run and inspect a binary on the host ──
        if tool_name.startswith("lldb_"):
            binary = tool_input.get("path", "?")
            return GuardDecision.ESCALATE, f"host execution: {tool_name} on '{binary}'"

        return GuardDecision.ALLOW, ""

    def _check_shell_command(self, command: str) -> tuple[GuardDecision, str]:
        """Inspect a shell command string for dangerous patterns.

        For heredoc commands (cat << EOF, python3 << 'SCRIPT', etc.) only the
        portion before the delimiter is scanned for dangerous patterns — the
        heredoc body is content being written, not commands being executed.
        Scanning the body causes false positives (e.g. the word 'Format' in a
        markdown report triggers the mkfs/format pattern).
        """
        # Strip heredoc body: keep only the command line (before the << marker).
        scan_target = command
        heredoc_pos = command.find("<<")
        if heredoc_pos != -1:
            # Take everything up to and including the first line that contains <<,
            # but only the portion of that line before the << itself.
            first_line_end = command.find("\n", heredoc_pos)
            scan_target = command[:heredoc_pos] if first_line_end != -1 else command[:heredoc_pos]

        # Dangerous command patterns → BLOCK
        match = _DANGEROUS_COMMANDS.search(scan_target)
        if match:
            return GuardDecision.BLOCK, f"dangerous command pattern: '{match.group(0).strip()}'"

        # Sudo → ESCALATE (not auto-block — might be intentional)
        if _SUDO_PATTERN.search(scan_target):
            return GuardDecision.ESCALATE, "command uses sudo"

        # Network / remote-access commands → ESCALATE
        net_match = _NETWORK_COMMANDS.search(scan_target)
        if net_match:
            return GuardDecision.ESCALATE, f"network command: '{net_match.group(0).strip()}'"

        # Package managers → ESCALATE (installing/removing software)
        pkg_match = _PACKAGE_MANAGERS.search(scan_target)
        if pkg_match:
            return GuardDecision.ESCALATE, f"package manager operation: '{pkg_match.group(0).strip()}'"

        # Arbitrary code execution → ESCALATE
        code_match = _CODE_EXECUTION.search(scan_target)
        if code_match:
            return GuardDecision.ESCALATE, f"inline code execution: '{code_match.group(0).strip()}'"

        # Script file execution → ESCALATE
        script_match = _SCRIPT_EXECUTION.search(command)
        if script_match:
            return GuardDecision.ESCALATE, f"script execution: '{script_match.group(0).strip()}'"

        # Sensitive path targets → ESCALATE
        # Only check if command is writing/modifying (not reading)
        write_indicators = re.compile(r"\b(tee|mv|cp|install|sed\s+-i|chmod|chown)\b")
        if write_indicators.search(command) and _SENSITIVE_PATHS.search(command):
            return GuardDecision.ESCALATE, f"modifying sensitive path"

        return GuardDecision.ALLOW, ""
