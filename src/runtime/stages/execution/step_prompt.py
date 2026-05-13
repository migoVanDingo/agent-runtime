"""Per-step system prompt builder for ExecutionStage."""
from __future__ import annotations

from planning.schema import Plan, Step, StepStatus
from session_paths import build_analysis_manifest


def step_system(
    plan: Plan,
    current_step: Step,
    agent_system: str,
    rag_context: str = "",
    step_display: int = 0,
) -> str:
    """Build the per-step system prompt showing plan progress."""
    lines = []
    for display_num, s in enumerate(plan.steps, start=1):
        if s.status == StepStatus.COMPLETED:
            marker = "✓"
        elif s.step == current_step.step:
            marker = "→"
        else:
            marker = " "
        lines.append(f"  {marker} Step {display_num}: {s.description}")

    tool_note = ""
    if current_step.tool:
        tool_note = f"\nYou have been given ONLY the '{current_step.tool}' tool for this step. Call it ONCE on the target specified in this step's description, then stop.\n"
        if current_step.tool == "write_file":
            tool_note += (
                "\nWhen writing a report or analysis file: include your complete interpretation "
                "and insights — not just raw tool output. The file should be self-contained "
                "and tell the full story of what was found. "
                "Do NOT attempt to read the output file before writing it — it may not exist yet.\n"
            )
        elif current_step.tool == "read_file":
            tool_note += (
                "\nRead ONLY the single file named in this step's description. "
                "Do not read any other files — other steps in this plan handle those.\n"
            )

    manifest = build_analysis_manifest()
    _disp = step_display or next(
        (i + 1 for i, s in enumerate(plan.steps) if s.step == current_step.step), 1
    )
    return (
        f"{agent_system}{rag_context}{manifest}\n\n"
        f"You are executing one step of a multi-step plan:\n" + "\n".join(lines) + "\n\n"
        f"Currently executing Step {_disp} of {len(plan.steps)}: "
        f"{current_step.description}\n"
        f"{tool_note}\n"
        f"IMPORTANT: Execute ONLY this step. Do not perform work belonging to other steps. "
        f"Do not create files or produce outputs that are not explicitly required by this step's description. "
        f"When this step is complete, stop."
    )
