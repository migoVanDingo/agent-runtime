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

# Matches paths containing at least one slash component (relative or absolute).
# We further filter out English prose fragments (e.g. "encrypt/decrypt",
# "usage/error") in _looks_like_real_path() below.
_PATH_RE = re.compile(r'(?<!\w)(?:\.{0,2}/)?[\w.\-]+(?:/[\w.\-]+)+')

def _looks_like_real_path(path: str) -> bool:
    """Return True only if this token looks like an actual filesystem path.

    Works for any project structure — no hardcoded directory names.
    Distinguishes real paths from English prose fragments that happen to
    contain '/', e.g. 'usage/error', 'encrypt/decrypt', 'strings/nm.'.

    Real path signals (any one is sufficient):
      - Starts with '/' (absolute path)
      - Any component has a dot-extension (file.c, report.md, data.json)
      - Any component starts with '_' (underscore-prefixed dirs/files)
      - Any component contains a digit
      - Path is long (>= 30 chars) — prose rarely runs that long

    Prose signals (all must be true to reject):
      - All components are lowercase-only words (no digits, no underscores,
        no extensions)
      - Path ends with '.' (sentence-ending period attached to last word)
      - Short (< 30 chars)
    """
    # Absolute path
    if path.startswith("/"):
        return True

    parts = path.split("/")

    # Any component has a dot-extension (non-empty string after last dot)
    for part in parts:
        _, dot, ext = part.rpartition(".")
        if dot and ext:
            return True

    # Any component starts with underscore or contains a digit
    for part in parts:
        if not part:
            continue
        if part[0] == "_":
            return True
        if any(c.isdigit() for c in part):
            return True

    # Long paths are unlikely to be English prose
    if len(path) >= 30:
        return True

    # Path ends with bare '.' → sentence-ending period attached to a word
    if path.endswith("."):
        return False

    # All components are purely lowercase alphabetic words → prose
    # "usage/error", "encrypt/decrypt", "strings/nm", "main/encrypt"
    def _all_alpha_lower(p: str) -> bool:
        return bool(p) and p.isalpha() and p.islower()
    if all(_all_alpha_lower(p) for p in parts if p):
        return False

    return True

# Matches bare filenames with extensions (e.g. proc-analysis.md, report.txt)
_FILENAME_RE = re.compile(r'\b[\w.\-]+\.(?:md|txt|json|csv|log|c|py|sh|bin|elf|out)\b')

# Paths under these directories are runtime-generated artifacts, NOT entities the user
# mentioned. Using them as correction candidates causes the agent to target wrong files
# (e.g. correcting "proc" → "_analysis/proc/ghidra_decompile.txt").
# .txt files in _analysis/ are paged tool output; operational dirs are never valid targets.
_ARTIFACT_SKIP_PATTERNS = (
    # Paged tool outputs: _analysis/**/*.txt
    ("_analysis/", ".txt"),
)
_OPERATIONAL_SKIP_DIRS = (
    "_sessions/", "_rag/", "_store/", "_logs/", "_metrics/", "_events/",
)


def _is_runtime_artifact(path: str) -> bool:
    """Return True if this path is a runtime-generated artifact, not a user entity."""
    norm = path.replace("\\", "/")
    # Operational directories — never correction candidates
    for skip in _OPERATIONAL_SKIP_DIRS:
        if f"/{skip}" in norm or norm.startswith(skip):
            return True
    # Paged tool output files: _analysis/**/*.txt
    for dir_prefix, ext in _ARTIFACT_SKIP_PATTERNS:
        if (f"/{dir_prefix}" in norm or norm.startswith(dir_prefix)) and norm.endswith(ext):
            return True
    return False


def _extract_entities(text: str) -> tuple[list[str], list[str]]:
    """Return (paths, filenames) found in text, preserving mention order.

    Filters out English prose fragments that happen to contain '/' but are
    not actual filesystem paths (e.g. 'encrypt/decrypt', 'usage/error').
    """
    paths = _PATH_RE.findall(text)
    filenames = _FILENAME_RE.findall(text)
    # deduplicate while preserving order; filter prose fragments
    seen: set[str] = set()
    unique_paths = []
    for p in paths:
        # Strip trailing punctuation that regex may absorb from sentence endings
        p = p.rstrip(".,;:!?)'\"")
        if p not in seen and _looks_like_real_path(p):
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

        # Remove runtime-generated artifact paths from the candidate pool.
        # These are files the runtime wrote automatically (paged tool outputs,
        # session logs, RAG stores) — not entities the user mentioned.
        # Keeping them causes false corrections like proc → _analysis/proc/ghidra_decompile.txt.
        ctx_paths = [p for p in ctx_paths if not _is_runtime_artifact(p)]

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
                # Never correct /tmp/ paths — these are planner-generated temp
                # output destinations and are authoritative by design.
                if path.startswith("/tmp/"):
                    continue
                if path not in ctx_paths:
                    candidate = self._best_path_match(path, ctx_paths)
                    if candidate:
                        # Don't strip a trailing filename: if the plan path is
                        # candidate/newfile.ext, the directory is valid and the file
                        # is new (to be created) — leave it alone.
                        if path.startswith(candidate.rstrip("/") + "/"):
                            continue
                        # If both paths share the same parent directory, the planner
                        # knows the correct directory and chose a different filename
                        # intentionally (e.g. proc_analysis_new.c vs proc_analysis.c).
                        # Correcting same-directory siblings destroys intentional naming.
                        path_dir = path.rsplit("/", 1)[0] if "/" in path else ""
                        cand_dir = candidate.rsplit("/", 1)[0] if "/" in candidate else ""
                        # Normalize: a relative path like "_analysis/proc" matches an
                        # absolute path that ends with "/_analysis/proc".
                        if path_dir and (
                            path_dir == cand_dir
                            or cand_dir.endswith("/" + path_dir)
                            or path_dir.endswith("/" + cand_dir)
                        ):
                            continue
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

        Only returns a match when there is genuine evidence of a relationship:
        same filename stem, or at least one shared path component.
        Returns None when there is no structural overlap — do not guess.
        """
        if not candidates:
            return None

        path_stem = path.rstrip("/").split("/")[-1]

        # Prefer candidate whose final component matches the stem
        for c in reversed(candidates):
            if c.rstrip("/").split("/")[-1] == path_stem:
                return c

        # Prefer candidate with the most shared path components
        path_parts = set(p for p in path.split("/") if p and p != ".")
        best = max(candidates, key=lambda c: len(set(c.split("/")) & path_parts))
        shared = len(set(best.split("/")) & path_parts)
        if shared > 0:
            return best

        # No structural relationship — do not correct.
        # A random "last resort" correction is worse than no correction.
        return None

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
