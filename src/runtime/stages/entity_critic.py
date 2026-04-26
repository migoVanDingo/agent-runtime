"""EntityCriticStage — corrects hallucinated file/path references in plans.

Phase 8 hardening: suspicious corrections (candidate has no slash — not a path,
or is a very short word) are reverted and surfaced via ASK_USER rather than
applied silently. The original `encryption/decryption`-style false positive that
corrupted step descriptions is exactly this pattern.

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


def _is_suspicious_candidate(old: str, new: str) -> bool:
    """Return True if the correction looks like a false positive.

    A correction is suspicious when the replacement is not a real filesystem
    path. Real path corrections replace one path with another path — they
    share structural properties: start with '/', './', '../', or a directory
    component that looks like an actual path (contains a file extension or
    starts with a known-prefix directory like '_tests/', 'src/', etc.).

    Specifically suspicious:
    - Replacement has no slash at all (bare word).
    - Replacement has a slash but both components look like plain English words
      (e.g. 'communication/rendering', 'encryption/decryption') — these are
      slash-separated English phrases extracted from assistant messages, not
      filesystem paths. Heuristic: if neither component contains a dot (file
      extension) and neither starts with '_' or '.', it's likely prose.
    - Replacement is extremely short (< 3 chars).
    """
    new = new.strip()
    if len(new) < 3:
        return True
    if not _PATH_HAS_SLASH.search(new):
        return True
    # Has a slash — check if it looks like a real path or a prose phrase.
    parts = new.split("/")
    has_extension = any("." in p for p in parts)
    has_path_marker = new.startswith(("./", "../", "/", "_", "."))
    looks_like_path_dir = any(
        p.startswith(("_", ".")) or p in {"src", "bin", "lib", "usr", "tmp", "etc", "var"}
        for p in parts
    )
    if has_extension or has_path_marker or looks_like_path_dir:
        # Extra check: old has a file extension but new does not.
        # This catches path→directory substitutions (e.g. /tmp/foo.asm → /Users/…/agent-runtime).
        old_stem = old.rstrip("/").split("/")[-1]
        new_stem = new.rstrip("/").split("/")[-1]
        old_has_ext = "." in old_stem
        new_has_ext = "." in new_stem
        if old_has_ext and not new_has_ext:
            return True
        return False
    # Slash-separated but no extension, no path marker, no known dir prefix
    # → likely a prose phrase like 'communication/rendering'
    return True


class EntityCriticStage(Stage):
    """Corrects hallucinated paths/filenames in the plan before validation.

    Reads:  context.plan, context.entity_context, context.user_message
    Writes: context.plan (corrected in-place)

    Phase 8 gate: suspicious corrections (candidate is not a real path) are
    reverted and the user is asked to confirm before they are applied. This
    prevents the entity critic from corrupting step descriptions with
    non-path words from the conversation.
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

        # If there are suspicious corrections, ask the user to confirm before using them.
        if suspicious:
            lines = [f"  • '{old}' → '{new}'" for _, old, new in suspicious]
            question = (
                "The entity critic found some corrections that look uncertain:\n"
                + "\n".join(lines)
                + "\n\nShould I apply these corrections, or leave the original values? "
                "Reply 'yes' to apply, 'no' to skip."
            )
            logger.info(f"  entity critic: {len(suspicious)} suspicious correction(s) — asking user")
            return StageResult(
                status=StageStatus.ASK_USER,
                updated_context=context,
                user_message=question,
            )

        return StageResult(status=StageStatus.OK, updated_context=context)
