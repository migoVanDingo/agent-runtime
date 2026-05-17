import re
from planning.schema import Plan, ActionType
from runtime.schema import ValidationStatus, ValidationResult
from app_config import config
from logger import get_logger

logger = get_logger(__name__)

# Action types that map to real toolsets (conversation is toolless, so it's always valid)
_TOOLSET_ACTION_TYPES = {a for a in ActionType if a != ActionType.CONVERSATION}

# Signals that the user explicitly wants new written output produced.
# Only triggers when a write-action verb is present — bare file extensions are
# NOT sufficient to trigger this (they appear in "iterate over proc_clone.c"
# and "which file is proc_clone.c", not just "write to proc_clone.c").
_WRITE_OUTPUT_RE = re.compile(
    r"\b(?:write|save|put|output)\s+(?:\w+\s+){0,3}(?:to|in)\b"   # write ... to / save ... in
    r"|\b(?:write|generate|create|produce)\s+a\s+(?:report|summary|file|analysis)\b"
    r"|\b(?:write|save|create|output)\s+(?:it\s+)?to\s+\S+\.(?:md|txt|c|h|py|js|ts|rs|go|java|json|csv|log)\b",
    re.IGNORECASE,
)


_SKILL_PREFIX = "skill:"


class PlanValidator:

    def __init__(
        self,
        registered_toolsets: set[str],
        registered_tools: set[str],
        registered_skills: set[str] | None = None,
    ):
        self._registered_toolsets = registered_toolsets
        self._registered_tools = registered_tools
        self._registered_skills = registered_skills or set()

    # ── Public API ──────────────────────────────────────────────────────────

    def validate(self, plan: Plan) -> ValidationResult:
        """Pre-expansion structural validation. No LLM call.

        Runs every rule. Rules that depend on concrete tools are deferred
        when the plan contains ``skill:*`` steps — those steps are opaque
        until SkillExpansionStage runs. Use ``validate_post_expansion`` to
        re-check the deferred rules against the expanded plan.
        """
        if not config.runtime.plan_validator.enabled:
            return ValidationResult(status=ValidationStatus.VALID)

        errors: list[str] = []
        self._check_step_count(plan, errors)
        if errors and plan.steps == []:
            return ValidationResult(
                status=ValidationStatus.INVALID,
                feedback="\n".join(errors),
            )
        self._check_numbering(plan, errors)
        self._check_action_types(plan, errors)
        self._check_descriptions(plan, errors)
        self._check_duplicate_consecutive(plan, errors)
        self._check_tools_registered(plan, errors)
        self._check_write_step(plan, errors, defer_when_skill_present=True)
        return self._finalize(errors)

    def validate_post_expansion(self, plan: Plan) -> ValidationResult:
        """Re-check ONLY the rules deferred pre-expansion against the expanded plan.

        Rules already validated pre-expansion are not re-run here — most
        notably, ``max_steps`` is intentionally not enforced post-expansion
        because skills naturally expand into many concrete steps (composing
        deep-disassembly + analyze-and-write produces 14+ steps, well above
        the planner-output cap). The pre-expansion check guards planner
        sprawl; post-expansion sprawl is intentional.

        Currently the only deferred rule is rule 7 (write_file presence when
        the query asks for written output).
        """
        if not config.runtime.plan_validator.enabled:
            return ValidationResult(status=ValidationStatus.VALID)
        errors: list[str] = []
        self._check_write_step(plan, errors, defer_when_skill_present=False)
        return self._finalize(errors)

    # ── Individual rules ────────────────────────────────────────────────────

    def _check_step_count(self, plan: Plan, errors: list[str]) -> None:
        max_steps = config.planning.max_steps
        if len(plan.steps) > max_steps:
            errors.append(f"Plan has {len(plan.steps)} steps but max is {max_steps}.")
        if len(plan.steps) == 0:
            errors.append("Plan has no steps.")

    def _check_numbering(self, plan: Plan, errors: list[str]) -> None:
        expected = list(range(1, len(plan.steps) + 1))
        actual = [s.step for s in plan.steps]
        if actual != expected:
            errors.append(
                f"Steps are not sequentially numbered 1..{len(plan.steps)}. Got: {actual}"
            )

    def _check_action_types(self, plan: Plan, errors: list[str]) -> None:
        for step in plan.steps:
            if step.action_type in _TOOLSET_ACTION_TYPES:
                if step.action_type.value not in self._registered_toolsets:
                    errors.append(
                        f"Step {step.step}: action_type '{step.action_type.value}' "
                        f"is not a registered toolset. Available: {sorted(self._registered_toolsets)}"
                    )

    def _check_descriptions(self, plan: Plan, errors: list[str]) -> None:
        for step in plan.steps:
            if not step.description or not step.description.strip():
                errors.append(f"Step {step.step}: empty description.")

    def _check_duplicate_consecutive(self, plan: Plan, errors: list[str]) -> None:
        for i in range(1, len(plan.steps)):
            prev_desc = plan.steps[i - 1].description.strip().lower()
            curr_desc = plan.steps[i].description.strip().lower()
            if prev_desc == curr_desc:
                errors.append(
                    f"Steps {plan.steps[i-1].step} and {plan.steps[i].step} "
                    f"have identical descriptions."
                )

    def _check_tools_registered(self, plan: Plan, errors: list[str]) -> None:
        for step in plan.steps:
            if step.action_type == ActionType.CONVERSATION:
                continue
            if step.tool is None:
                errors.append(
                    f"Step {step.step}: non-conversation step must declare a 'tool' field."
                )
            elif step.tool.startswith(_SKILL_PREFIX):
                skill_name = step.tool[len(_SKILL_PREFIX):]
                if skill_name not in self._registered_skills:
                    errors.append(
                        f"Step {step.step}: skill '{skill_name}' is not registered. "
                        f"Available skills: {sorted(self._registered_skills)}"
                    )
            elif step.tool not in self._registered_tools:
                errors.append(
                    f"Step {step.step}: tool '{step.tool}' is not registered. "
                    f"Available: {sorted(self._registered_tools)}"
                )

    def _check_write_step(
        self,
        plan: Plan,
        errors: list[str],
        *,
        defer_when_skill_present: bool,
    ) -> None:
        """Rule 7: query expects written output → plan must include write_file.

        Pre-expansion, this defers when a ``skill:*`` step exists (skill's
        ``expand()`` may produce the write_file step). Post-expansion, it
        always runs.
        """
        if not plan.original_query or not _WRITE_OUTPUT_RE.search(plan.original_query):
            return
        has_write = any(s.tool == "write_file" for s in plan.steps)
        if has_write:
            return
        if defer_when_skill_present:
            has_skill_step = any(
                (s.tool or "").startswith(_SKILL_PREFIX) for s in plan.steps
            )
            if has_skill_step:
                return
        errors.append(
            "Query expects written output (file path or 'write/save/generate' phrasing) "
            "but the plan contains no write_file step. Add a step to write the output."
        )

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _finalize(self, errors: list[str]) -> ValidationResult:
        if errors:
            feedback = "\n".join(errors)
            logger.info(f"  validation FAILED:\n    " + "\n    ".join(errors))
            return ValidationResult(status=ValidationStatus.INVALID, feedback=feedback)
        logger.info("  validation: VALID")
        return ValidationResult(status=ValidationStatus.VALID)
