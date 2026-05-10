"""Standalone reconstruction verification skill.

The fix-loop (run diff → identify divergence → fix → re-run diff)
is owned by ContinuationStage. This skill declares only the initial
diff and the per-iteration fix steps — never how many times to loop.
"""
from __future__ import annotations
import re
from planning.schema import Step, ActionType
from runtime.schema import ContinuationDecision
from skills.base import Skill, SkillContext
from skills.criteria import CompletionCriteria, StructuralCriteria, diff_behavior_all_match


class TestReconstruction(Skill):
    name = "test-reconstruction"
    intent = (
        "Use this skill when the user wants to test, verify, or iteratively fix a "
        "reconstructed source file against an original binary executable. The skill "
        "constructs test cases based on prior analysis of the oracle binary's CLI "
        "interface, runs diff_behavior to compare outputs, and ContinuationStage "
        "drives the fix-loop until all cases pass. Works for any binary, not just "
        "encrypt/decrypt programs."
    )

    pattern = re.compile(
        r"iterate\s+on\s+(?:the\s+)?(?:code|source|clone|reconstruction)"
        r"|test\s+(?:the\s+)?(?:clone|reconstruction|source)\s+(?:against|vs)"
        r"|verify\s+(?:the\s+)?reconstruction"
        r"|does\s+\S+\s+match\s+(?:the\s+)?(?:original|binary)"
        r"|check\s+if\s+\S+\s+(?:matches|is\s+correct)"
        r"|run\s+diff_behavior"
        r"|behavioral\s+(?:test|diff|comparison)",
        re.IGNORECASE,
    )

    _target_re = re.compile(r"(?:^|\s)(_?[\w./\-]+(?:/[\w.\-]+)*)\b")

    @property
    def completion_criteria(self) -> CompletionCriteria:
        return StructuralCriteria(
            tool_name="diff_behavior",
            predicate=diff_behavior_all_match,
            on_met=ContinuationDecision.SYNTHESIZE,
        )

    def expand(self, ctx: SkillContext) -> list[Step]:
        message = ctx.original_query
        oracle, candidate = self._extract_paths(message)
        n = ctx.starting_step_number

        steps = []

        if oracle is None or candidate is None:
            # Paths unclear — ask the model to identify them first
            steps.append(Step(
                step=n,
                description=(
                    f"Identify the oracle binary and candidate source file from the "
                    f"user's message and conversation context.\n"
                    f"Detected oracle: {oracle or 'unknown — check conversation for recent binary paths'}\n"
                    f"Detected candidate: {candidate or 'unknown — look for recent .c/.py files in _tests/'}\n\n"
                    f"Look for files matching *_clone.c or *_fixed.c in _tests/. "
                    f"Once identified, use them in the diff_behavior call."
                ),
                action_type=ActionType.CONVERSATION,
                tool=None,
            ))
            n += 1

        steps.append(Step(
            step=n,
            description=self._diff_step_description(oracle, candidate),
            action_type=ActionType.SHELL,
            tool="diff_behavior",
        ))

        return steps

    def continuation_steps(
        self, ctx: SkillContext, prior_results: list[Step],
    ) -> list[Step] | None:
        """Return steps for one fix-and-retest iteration.

        The infrastructure (not the skill) decides how many times this is called
        — bounded by max_iterations and gated by StructuralCriteria above.
        """
        oracle, candidate = self._extract_paths(ctx.original_query)
        n = ctx.starting_step_number
        return [
            Step(
                step=n,
                description=(
                    "Read the most recent DiffReport. Identify the failing test "
                    "cases and what each mismatch implies about the candidate's "
                    "implementation."
                ),
                action_type=ActionType.CONVERSATION,
                tool=None,
            ),
            Step(
                step=n + 1,
                description=(
                    f"Read the current candidate source from {candidate or '<candidate>'} "
                    f"with read_file. You must see what is on disk before editing it."
                ),
                action_type=ActionType.FILE_IO,
                tool="read_file",
            ),
            Step(
                step=n + 2,
                description=(
                    f"Fix every bug identified in the DiffReport analysis above.\n"
                    f"Write the corrected source to {candidate or '<candidate>'} with write_file.\n\n"
                    f"Base your fix on:\n"
                    f"  - The specific mismatch pattern from the DiffReport (which cases fail, "
                    f"how outputs differ — length, content, exit code, stderr)\n"
                    f"  - Your prior analysis of the oracle binary from this conversation or "
                    f"the artifact store (algorithm details, constants, implementation specifics)\n"
                    f"  - What you read from the current candidate source in the previous step\n\n"
                    f"Do not guess: use what the analysis actually showed about the oracle's "
                    f"implementation. If you need to re-examine the oracle, do so before writing."
                ),
                action_type=ActionType.FILE_IO,
                tool="write_file",
            ),
            Step(
                step=n + 3,
                description=self._diff_step_description(oracle, candidate),
                action_type=ActionType.SHELL,
                tool="diff_behavior",
            ),
        ]

    def _diff_step_description(self, oracle: str | None, candidate: str | None) -> str:
        _oracle = oracle or '<oracle_path>'
        _cand = candidate or '<candidate_path>'
        return (
            f"Call diff_behavior to compare {_oracle} (oracle) vs {_cand} (candidate).\n\n"
            f"BEFORE calling: recall what you know about {_oracle}'s command-line interface.\n"
            f"Check the conversation history and artifact store for prior analysis of {_oracle}:\n"
            f"  - What arguments does it accept? (use --help, strings output, or prior recon)\n"
            f"  - What modes/flags does it have?\n"
            f"  - What are meaningful boundary inputs for its functionality?\n\n"
            f"Construct test_cases that exercise the main functionality with varied inputs:\n"
            f"  - At least 4-6 cases covering different modes, input sizes, and edge cases\n"
            f"  - Each case: {{\"id\": \"<descriptive_name>\", \"args\": [<argv list>]}}\n"
            f"  - Base them on actual CLI interface — do NOT guess or use generic placeholders\n\n"
            f"Then call diff_behavior with:\n"
            f"  oracle_path: {_oracle}\n"
            f"  oracle_type: native_binary\n"
            f"  candidate_path: {_cand}\n"
            f"  candidate_type: c_source (or cpp_source/python_source as appropriate)\n"
            f"  test_cases: [<your constructed cases based on the binary's actual interface>]"
        )

    # Finds "binary_name vs/and path" or "path vs/and binary_name" patterns
    # where binary_name has no extension and path has a source extension or /
    _CMP_RE = re.compile(
        r"(?:"
        # pattern A: binary_name KEYWORD path
        r"(?:^|[\s,;])([\w\-]+)\s+(?:vs\.?|and|against|versus)\s+(?:[\w./\-]+/[\w.\-]+|\S+\.(?:c|cpp|py|h))"
        r"|"
        # pattern B: path KEYWORD binary_name
        r"(?:[\w./\-]+/[\w.\-]+|\S+\.(?:c|cpp|py|h))\s+(?:vs\.?|and|against|versus)\s+([\w\-]+)"
        r"|"
        # pattern C: "X against/vs Y" where X looks like a binary (no /, no ext)
        r"(?:^|[\s,;])([\w\-]+)\s+(?:vs\.?|against|versus)\s+([\w\-]+)"
        r")",
        re.IGNORECASE,
    )

    _NOISE_WORDS = {
        "the", "a", "an", "and", "vs", "or", "is", "it", "my", "your",
        "its", "this", "that", "over", "on", "of", "to", "in", "for",
        "iterate", "test", "verify", "run", "does", "check", "match",
        "compare", "against", "diff", "patch", "make", "write", "fix",
        "please", "just", "now", "then", "so", "but", "if", "when",
        "can", "will", "shall", "may", "should", "would", "could",
        "have", "has", "had", "be", "been", "do", "did", "get",
        "want", "need", "use", "try", "also",
        # Role labels (not filenames)
        "oracle", "candidate", "binary", "source", "original", "clone",
        "reconstruction", "implementation", "executable",
    }
    _SRC_EXTS = (".c", ".cpp", ".py", ".h")
    _TEXT_EXTS = (".md", ".txt", ".json", ".yaml", ".yml", ".log", ".csv")

    def _extract_paths(self, message: str) -> tuple[str | None, str | None]:
        oracle, candidate = None, None

        # Pass 1: find path-style tokens (contain / or have source extension)
        for m in self._target_re.finditer(message):
            tok = m.group(1)
            if tok.startswith("http"):
                continue
            lower = tok.lower()
            if any(lower.endswith(ext) for ext in self._SRC_EXTS):
                candidate = tok
            elif "clone" in lower or "fixed" in lower:
                candidate = tok
            elif "/" in tok and oracle is None and not any(lower.endswith(e) for e in self._SRC_EXTS):
                oracle = tok

        # Pass 2: extract bare binary name from comparison context
        # e.g. "proc and proc_clone.c", "proc_clone.c and proc"
        if oracle is None:
            for m in self._CMP_RE.finditer(message):
                # Groups: A=name before keyword, B=name after keyword, C/D=both sides of X vs Y
                for grp in (m.group(1), m.group(2), m.group(3), m.group(4)):
                    if not grp:
                        continue
                    low = grp.lower()
                    if low in self._NOISE_WORDS:
                        continue
                    if any(low.endswith(e) for e in self._SRC_EXTS + self._TEXT_EXTS):
                        continue
                    if "clone" in low or "fixed" in low:
                        continue
                    # Looks like a binary name
                    oracle = grp
                    break
                if oracle is not None:
                    break

        return oracle, candidate
