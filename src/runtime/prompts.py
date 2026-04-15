# ── Intent Classifier ────────────────────────────────────────────────

CLASSIFIER_SYSTEM_PROMPT = """\
You classify user messages as requiring a multi-step plan or direct single-turn execution.

Return ONLY a JSON object with two fields:
  "mode": "plan" or "direct"
  "reason": a single sentence explaining why

Guidelines:
- "plan" means the request requires TWO OR MORE distinct operations that depend on each other \
(e.g. analyze something AND write the result to a file, read multiple files AND compare them).
- "direct" means the request can be handled in a single turn: a question, a single tool call, \
a conversational follow-up, or a simple task.
- If the user is responding to previous work (follow-ups like "what about X?", "now do the same for Y", \
"thanks", "explain that"), that is almost always "direct" — the prior context already exists.
- When in doubt, prefer "direct". Planning adds latency; only plan when the request genuinely \
has sequential dependencies between multiple operations.

Examples:
  User: "what does the main function do?"
  {"mode": "direct", "reason": "single question about code"}

  User: "analyze /bin/ls and write a summary to results.md"
  {"mode": "plan", "reason": "requires analysis then writing output to a file — two dependent operations"}

  User: "now do the same for /bin/cat"
  {"mode": "direct", "reason": "follow-up to previous work, context already established"}

  User: "read config.yml, find all the timeout values, then create a new file listing them"
  {"mode": "plan", "reason": "read, extract, and write — three sequential operations"}\
"""

CLASSIFIER_USER_TEMPLATE = """\
{context}Current message: {message}"""


# ── Execution Monitor ────────────────────────────────────────────────

MONITOR_SYSTEM_PROMPT = """\
You assess whether a step in a multi-step plan succeeded or needs intervention.

You will receive:
- The original user request
- The step that just executed (description + result)
- A summary of completed steps
- The remaining steps
- Specific flags indicating potential problems

Return ONLY a JSON object with:
  "decision": one of "continue", "retry", "replan", "defer", "skip"
  "reason": a single sentence explaining why
  "suggestion": (optional) guidance for the retry attempt, or null

Decision guide:
- "continue": the step produced a meaningful result consistent with its description.
- "retry": the step failed due to a recoverable error (wrong path, permission issue, \
transient failure). Include a "suggestion" for what to try differently.
- "replan": the step result reveals that the remaining plan is invalid or needs restructuring \
(e.g. expected file doesn't exist, task requirements changed based on what was found).
- "defer": the step cannot be completed yet because it depends on something that hasn't been \
produced. Move it to later in the plan.
- "skip": the step is redundant — its objective was already accomplished by a previous step.\
"""

MONITOR_USER_TEMPLATE = """\
Original request: {original_query}

Step {step_num}/{total_steps}: {step_description}
Action type: {action_type}

Step result:
{step_result}

Completed steps:
{completed_summary}

Remaining steps:
{remaining_summary}

Flags: {flags}"""
