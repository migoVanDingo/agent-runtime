"""EntityCriticStage — corrects hallucinated file/path references in plans.

Suspicious corrections (prose phrases, system paths, wrong-extension substitutions)
are reverted silently rather than applied or escalated. Only unambiguously valid
path corrections are applied.

Skipped (no-op) if no entity context exists (nothing to compare against).
"""
from __future__ import annotations
import re
from runtime.entity_critic import EntityCritic
from runtime.pipeline_context import PipelineContext
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus
from runtime.utils import banner
from logger import get_logger

logger = get_logger(__name__)

# A legitimate path candidate always contains at least one slash.
_PATH_HAS_SLASH = re.compile(r"/")

# Correction log lines look like:
#   "step N: 'old' → 'new' (not in conversation context)"
_CORRECTION_RE = re.compile(r"step \d+: '(.+?)' → '(.+?)' \(not in conversation context\)")


def _looks_like_path(token: str) -> bool:
    """Return True if token plausibly represents a filesystem path.

    A token is path-like if it:
    - starts with a path prefix (/, ./, ../, _, .)
    - contains a file extension in the final component
    - has a component that looks like a known directory name
    """
    if not token:
        return False
    if token.startswith(("/", "./", "../", "_", ".")):
        return True
    parts = token.split("/")
    last = parts[-1]
    if "." in last and not last.startswith("."):
        return True  # has a file extension
    known_dirs = {"src", "bin", "lib", "usr", "tmp", "etc", "var", "tests", "data",
                  "runtime", "tools", "scripts", "docs", "build", "dist"}
    if any(p in known_dirs or p.startswith(("_", ".")) for p in parts):
        return True
    return False


_UPPER_SLASH_RE = re.compile(r'^[A-Z0-9_]+(?:/[A-Z0-9_]+)+$')

# System device/pseudo-filesystem paths that should never be "corrected".
_SYSTEM_PATH_PREFIXES = ("/dev/", "/proc/", "/sys/", "/etc/", "/usr/")


def _is_suspicious_candidate(old: str, new: str) -> bool:
    """Return True if the correction looks like a false positive.

    A correction is suspicious when:
    - old is a prose phrase (XOR/shift/add, padding/unpadding, ECB/CBC) not a path
    - old is a system device path (/dev/null, /dev/stderr, etc.)
    - The replacement is not a real filesystem path
    - Structural mismatches (short, extension mismatch, etc.)
    """
    new = new.strip()
    old = old.strip()

    if len(new) < 3:
        return True

    # All-caps slash-separated tokens are crypto/config constants (BLOCK/ROUNDS,
    # ECB/CBC/CTR, IV/KEY, etc.), never filesystem paths.
    if _UPPER_SLASH_RE.match(old):
        return True

    # System pseudo-filesystem paths are real paths — never replace them.
    if any(old.startswith(prefix) for prefix in _SYSTEM_PATH_PREFIXES):
        return True

    # If old contains a slash but doesn't look like a path, it's a prose phrase
    # (XOR/shift/add, padding/unpadding) — correction is nonsensical.
    if "/" in old and not _looks_like_path(old):
        return True

    if not _PATH_HAS_SLASH.search(new):
        return True

    # new has a slash — check if it looks like a real path or a prose phrase.
    parts = new.split("/")
    has_extension = any("." in p for p in parts)
    has_path_marker = new.startswith(("./", "../", "/", "_", "."))
    looks_like_path_dir = any(
        p.startswith(("_", ".")) or p in {"src", "bin", "lib", "usr", "tmp", "etc", "var"}
        for p in parts
    )
    if has_extension or has_path_marker or looks_like_path_dir:
        old_stem = old.rstrip("/").split("/")[-1]
        new_stem = new.rstrip("/").split("/")[-1]
        old_has_ext = "." in old_stem
        new_has_ext = "." in new_stem
        # old has a file extension but new does not → substituting wrong shape
        if old_has_ext and not new_has_ext:
            return True
        # old has no extension but new has a document extension (.md, .txt, .json)
        # → replacing a binary/script path with a report path, always wrong
        _DOC_EXT = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".rst"}
        new_ext = "." + new_stem.rsplit(".", 1)[-1] if "." in new_stem else ""
        if not old_has_ext and new_ext in _DOC_EXT:
            return True
        return False
    # Slash-separated but no extension, no path marker, no known dir prefix
    # → likely a prose phrase like 'communication/rendering'
    return True


class EntityCriticStage(Stage):
    """Corrects hallucinated paths/filenames in the plan before validation.

    Reads:  context.plan, context.entity_context, context.user_message
    Writes: context.plan (corrected in-place)

    Suspicious corrections are reverted silently; the user is never interrupted.
    """

    name = "EntityCriticStage"

    def __init__(self, entity_critic: EntityCritic) -> None:
        self._entity_critic = entity_critic

    def run(self, context: PipelineContext) -> StageResult:
        # No-op for direct mode.
        if context.classification is None or context.classification.mode != "plan":
            return StageResult(status=StageStatus.OK, updated_context=context)

        # No plan to correct.
        if context.plan is None:
            return StageResult(status=StageStatus.OK, updated_context=context)

        # No entity context — nothing to compare against, skip.
        if not context.entity_context:
            return StageResult(status=StageStatus.OK, updated_context=context)

        logger.info(banner("Entity critic"))
        plan, corrections = self._entity_critic.correct(
            context.plan,
            context.entity_context,
            user_message=context.user_message,
        )

        if not corrections:
            logger.info("  no corrections needed")
            context.plan = plan
            return StageResult(status=StageStatus.OK, updated_context=context)

        # Partition corrections into clean and suspicious.
        clean: list[str] = []
        suspicious: list[tuple[str, str, str]] = []  # (step_num_str, old, new)

        for msg in corrections:
            m = _CORRECTION_RE.match(msg)
            if m:
                old, new = m.group(1), m.group(2)
                if _is_suspicious_candidate(old, new):
                    suspicious.append((msg, old, new))
                else:
                    clean.append(msg)
            else:
                clean.append(msg)

        # Revert suspicious corrections before committing the plan.
        for _msg, old, new in suspicious:
            for step in plan.steps:
                if new in step.description:
                    step.description = step.description.replace(new, old)
            logger.info(f"  suspicious correction reverted: '{old}' ← '{new}' (no slash in candidate)")

        for msg in clean:
            logger.info(f"  corrected: {msg}")

        context.plan = plan
        return StageResult(status=StageStatus.OK, updated_context=context)
