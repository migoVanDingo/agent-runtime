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

    def validate(self, plan: Plan) -> ValidationResult:
        """Structural validation of a plan. No LLM call."""
        if not config.runtime.plan_validator.enabled:
            return ValidationResult(status=ValidationStatus.VALID)

        errors = []

        # 1. Step count
        max_steps = config.planning.max_steps
        if len(plan.steps) > max_steps:
            errors.append(f"Plan has {len(plan.steps)} steps but max is {max_steps}.")

        if len(plan.steps) == 0:
            errors.append("Plan has no steps.")
            return ValidationResult(
                status=ValidationStatus.INVALID,
                feedback="\n".join(errors),
            )

        # 2. Sequential numbering
        expected = list(range(1, len(plan.steps) + 1))
        actual = [s.step for s in plan.steps]
        if actual != expected:
            errors.append(
                f"Steps are not sequentially numbered 1..{len(plan.steps)}. "
                f"Got: {actual}"
            )

        # 3. Action types exist as registered toolsets
        for step in plan.steps:
            if step.action_type in _TOOLSET_ACTION_TYPES:
                if step.action_type.value not in self._registered_toolsets:
                    errors.append(
                        f"Step {step.step}: action_type '{step.action_type.value}' "
                        f"is not a registered toolset. Available: {sorted(self._registered_toolsets)}"
                    )

        # 4. Non-empty descriptions
        for step in plan.steps:
            if not step.description or not step.description.strip():
                errors.append(f"Step {step.step}: empty description.")

        # 5. Duplicate consecutive steps (same description)
        for i in range(1, len(plan.steps)):
            prev_desc = plan.steps[i - 1].description.strip().lower()
            curr_desc = plan.steps[i].description.strip().lower()
            if prev_desc == curr_desc:
                errors.append(
                    f"Steps {plan.steps[i-1].step} and {plan.steps[i].step} "
                    f"have identical descriptions."
                )

        # 6. Tool field validation — must be a real registered tool, skill reference, or null
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

        # 7. Write-step completeness: if the query signals written output, the plan
        #    must include at least one write_file step.
        if plan.original_query and _WRITE_OUTPUT_RE.search(plan.original_query):
            has_write = any(s.tool == "write_file" for s in plan.steps)
            if not has_write:
                errors.append(
                    "Query expects written output (file path or 'write/save/generate' phrasing) "
                    "but the plan contains no write_file step. Add a step to write the output."
                )

        if errors:
            feedback = "\n".join(errors)
            logger.info(f"  validation FAILED:\n    " + "\n    ".join(errors))
            return ValidationResult(
                status=ValidationStatus.INVALID,
                feedback=feedback,
            )

        logger.info("  validation: VALID")
        return ValidationResult(status=ValidationStatus.VALID)
