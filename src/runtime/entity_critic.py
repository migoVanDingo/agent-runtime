"""Entity critic — corrects hallucinated file references in plans.

After the planner generates a plan, this pass checks each step description
against the conversation context. If a step references a path that never
appeared in the conversation but the context contains a concrete alternative,
the incorrect reference is corrected in-place before execution begins.

This catches the class of error where the planner invents a generic path
(e.g. '/tmp/target') when the user was referring to a specific file already
discussed in the conversation.
"""

import re
from planning.schema import Plan
from logger import get_logger

logger = get_logger(__name__)

# Matches paths containing at least one slash component (relative or absolute)
_PATH_RE = re.compile(r'(?<!\w)(?:\.{0,2}/)?[\w.\-]+(?:/[\w.\-]+)+')

# Matches bare filenames with extensions (e.g. proc-analysis.md, report.txt)
_FILENAME_RE = re.compile(r'\b[\w.\-]+\.(?:md|txt|json|csv|log|c|py|sh|bin|elf|out)\b')


def _extract_entities(text: str) -> tuple[list[str], list[str]]:
    """Return (paths, filenames) found in text, preserving mention order."""
    paths = _PATH_RE.findall(text)
    filenames = _FILENAME_RE.findall(text)
    # deduplicate while preserving order
    seen: set[str] = set()
    unique_paths = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique_paths.append(p)
    seen_f: set[str] = set()
    unique_files = []
    for f in filenames:
        if f not in seen_f:
            seen_f.add(f)
            unique_files.append(f)
    return unique_paths, unique_files


class EntityCritic:
    """Corrects plan step descriptions that reference entities not in conversation context."""

    def correct(self, plan: Plan, context: str | None, user_message: str | None = None) -> tuple[Plan, list[str]]:
        """Check plan steps against context and apply corrections.

        Entities mentioned in user_message are authoritative — they were
        explicitly stated by the user and must never be corrected away, even
        if they don't appear in prior conversation history.

        Returns the (possibly modified) plan and a list of human-readable
        correction messages for logging.
        """
        if not context:
            return plan, []

        ctx_paths, ctx_files = _extract_entities(context)

        # Extract authoritative entities from the current user message.
        # These take precedence over history-based corrections.
        auth_paths: set[str] = set()
        auth_files: set[str] = set()
        if user_message:
            up, uf = _extract_entities(user_message)
            auth_paths = set(up)
            auth_files = set(uf)
            # Also add them to context so they're valid candidates
            for p in up:
                if p not in ctx_paths:
                    ctx_paths.append(p)
            for f in uf:
                if f not in ctx_files:
                    ctx_files.append(f)

        if not ctx_paths and not ctx_files:
            return plan, []

        corrections: list[str] = []

        for step in plan.steps:
            desc = step.description
            step_paths, step_files = _extract_entities(desc)

            # Check paths in this step against context paths
            for path in step_paths:
                # Never correct a path the user explicitly stated
                if path in auth_paths:
                    continue
                if path not in ctx_paths:
                    candidate = self._best_path_match(path, ctx_paths)
                    if candidate:
                        step.description = step.description.replace(path, candidate)
                        corrections.append(
                            f"step {step.step}: '{path}' → '{candidate}' (not in conversation context)"
                        )
                        logger.info(f"  entity critic: step {step.step} path '{path}' → '{candidate}'")

            # Re-extract after path corrections (description may have changed)
            _, step_files_after = _extract_entities(step.description)

            # Check .md output filenames against context filenames
            for fname in step_files_after:
                if not fname.endswith(".md"):
                    continue
                # Never correct a filename the user explicitly stated
                if fname in auth_files:
                    continue
                if fname not in ctx_files:
                    candidate = self._best_file_match(fname, ctx_files)
                    if candidate:
                        step.description = step.description.replace(fname, candidate)
                        corrections.append(
                            f"step {step.step}: '{fname}' → '{candidate}' (not in conversation context)"
                        )
                        logger.info(f"  entity critic: step {step.step} file '{fname}' → '{candidate}'")

        return plan, corrections

    def _best_path_match(self, path: str, candidates: list[str]) -> str | None:
        """Find the most likely intended path from context candidates.

        Strategy: prefer candidates that share the most path components
        or the same filename stem. Falls back to the last-mentioned candidate
        if there's only one option.
        """
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[-1]

        path_stem = path.rstrip("/").split("/")[-1]

        # Prefer candidate whose final component matches the stem
        for c in reversed(candidates):
            if c.rstrip("/").split("/")[-1] == path_stem:
                return c

        # Prefer candidate with the most shared path components
        path_parts = set(path.split("/"))
        best = max(candidates, key=lambda c: len(set(c.split("/")) & path_parts))
        shared = len(set(best.split("/")) & path_parts)
        if shared > 0:
            return best

        # Last resort: most recently mentioned candidate
        return candidates[-1]

    def _best_file_match(self, fname: str, candidates: list[str]) -> str | None:
        """Find the most likely intended filename from context candidates.

        Only returns a match if there's exactly one .md file in context —
        ambiguous cases are left alone to avoid overcorrecting.
        """
        md_candidates = [f for f in candidates if f.endswith(".md")]
        if not md_candidates:
            return None
        if len(md_candidates) == 1:
            return md_candidates[-1]
        # Multiple .md files in context — don't guess, leave it to the planner
        return None
