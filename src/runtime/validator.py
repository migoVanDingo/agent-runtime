from planning.schema import Plan, ActionType
from runtime.schema import ValidationStatus, ValidationResult
from app_config import config
from logger import get_logger

logger = get_logger(__name__)

# Action types that map to real toolsets (conversation is toolless, so it's always valid)
_TOOLSET_ACTION_TYPES = {a for a in ActionType if a != ActionType.CONVERSATION}


class PlanValidator:

    def __init__(self, registered_toolsets: set[str], registered_tools: set[str]):
        self._registered_toolsets = registered_toolsets
        self._registered_tools = registered_tools

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

        # 6. Tool field validation — must be a real registered tool or null
        for step in plan.steps:
            if step.action_type == ActionType.CONVERSATION:
                continue
            if step.tool is None:
                errors.append(
                    f"Step {step.step}: non-conversation step must declare a 'tool' field."
                )
            elif step.tool not in self._registered_tools:
                errors.append(
                    f"Step {step.step}: tool '{step.tool}' is not registered. "
                    f"Available: {sorted(self._registered_tools)}"
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
