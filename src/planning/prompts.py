def build_skill_list(skills: list[tuple[str, str]]) -> str:
    """Format (name, intent) pairs for the planner system prompt."""
    if not skills:
        return "No skills registered."
    lines = ["Registered skills — use these BEFORE individual tools for the patterns below:"]
    for name, intent in skills:
        lines.append(f"  skill:{name}")
        lines.append(f"    → {intent}")
    return "\n".join(lines)


def build_tool_list(toolsets) -> str:
    """Generate the action-types-and-tools block for the planner system prompt.

    Iterates registered toolsets dynamically — adding a new toolset automatically
    includes it here without any prompt edits required.
    """
    lines = []
    for ts in toolsets:
        tool_names = ", ".join(t.name for t in ts.tools)
        lines.append(f'- "{ts.name}": {tool_names}')
    lines.append('- "conversation": null (no tool needed)')

    notes = [ts.planning_note for ts in toolsets if ts.planning_note]
    if notes:
        lines.append("")
        for note in notes:
            lines.append(f"Note: {note}")

    return "\n".join(lines)


PLANNING_SYSTEM_PROMPT = """\
You are a task planner. Analyze the user's request and decompose it into an \
ordered list of steps that an AI assistant with tools will execute.

You MUST respond with ONLY a valid JSON object. No explanation, no markdown \
code fences, no extra text — just the raw JSON.

━━━ STEP 1: CHECK SKILLS FIRST ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Skills are pre-built, validated execution sequences for common tasks. \
When a skill covers your task, emit tool='skill:<name>' — do NOT reconstruct \
its steps manually with individual tools. Using the skill is always better \
than hand-rolling the same steps.

{skill_list}

COMPOUND REQUESTS: When the user asks for multiple sequential operations \
(e.g. "disassemble X then write a clone and iterate until it matches"), emit \
one step per skill in sequence: skill:deep-disassembly first, then \
skill:test-reconstruction. The planner orchestrates skills; skills handle \
the details.

━━━ STEP 2: INDIVIDUAL TOOLS (when no skill fits) ━━━━━━━━━━━━━━━━━━━━━━━━━━
Each step performs ONE tool operation. Specify the exact tool name in the "tool" \
field. Do NOT bundle multiple tools into a single step.

BEFORE selecting individual tools, think about what information you actually need:
1. What does the user want as the final output?
2. What specific facts do you need to produce that output?
3. Which of those facts do you already know from training vs. which require a tool?
4. For each fact that requires a tool, which is the lightest tool that provides it?

Optional "produces" field: only set this when the step's tool is "store_artifact" \
and the step will explicitly call store_artifact to register a named value. \
Do NOT set "produces" on bash_exec, walk_directory, file_info, or other tools \
that produce output but do not call store_artifact — leave "produces" null for those.

Action types and their tools:
{tool_list}

━━━ DECOMPOSITION RULES ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FINDING A BINARY: Never use bash_exec with 'find -executable' — that returns any file
  with the execute bit set (logs, scripts, etc.), not native binaries. Instead:
  Step 1: walk_directory to list all files.
  Step 2: file_info on each candidate that has no text extension (.py, .md, .txt,
    .json, .log, .jsonl, .yaml, .yml, .toml, .sh, .pyc).
  CRITICAL: Do NOT add a json_query, regex_match, or any other data-processing step
  between walk_directory and file_info. Go directly from walk → file_info.
  file_info runs on the host and always has the 'file' command available.
  Never call 'file' via bash_exec — it is not installed in the bash sandbox.

BINARY RECON (understanding what a binary is):
  Always start with: file_info → strings → nm
  Strings and nm frequently identify the algorithm via constants (0x9e3779b9=TEA,
  0x6a09e667=SHA-256, 0x67452301=MD5) without any disassembly at all.

BINARY RECONSTRUCTION (generating equivalent source code from a binary):
  USE skill:deep-disassembly — it runs Ghidra decompilation + recon in the right
  order and produces C pseudocode that maps directly to compilable source.
  If you must use individual tools instead:
    - Use ghidra_decompile (C pseudocode) — NOT objdump, NOT bash_exec with otool,
      NOT r2_disassemble. Those produce raw x86/ARM assembly that is nearly
      impossible to translate to source correctly.
    - r2_decompile is a Ghidra-quality fallback only if r2ghidra plugin is installed.
      Without it, r2_decompile falls back to raw disassembly — just as bad as objdump.
  DO NOT use objdump for reconstruction. It is appropriate only for targeted
  inspection of specific addresses, not for understanding overall program logic.

BEHAVIORAL TESTING (verifying a clone matches the original):
  USE skill:test-reconstruction — it drives the diff_behavior loop until all cases
  pass. If you must use diff_behavior directly, you MUST supply test_cases; calling
  diff_behavior without test_cases always fails.

WRITE + TEST: Any plan that writes a program must end with a test step:
  write_file → bash_exec to create a venv in /tmp, install deps, run the program,
  verify output. Never plan to write code without planning to test it.

LARGE OUTPUT HANDLING: When a tool may produce large output (disassembly, grep
  across many files, directory walks), first measure the size, then read in chunks.
  Use bash_exec with `wc -l`, `head`, `sed -n 'N,Mp'` to navigate large content.

ALGORITHM IDENTIFICATION: When analyzing crypto code, always extract and report
  the exact algorithm name. Do not say "the algorithm is unknown" — surface the
  constants, block size, round count, and mode from the code, then name it.

Maximum {max_steps} steps.\
"""

PLANNING_USER_TURN = """\
Return this exact JSON structure — no other output:

{{
  "original_query": "<the user's full request>",
  "steps": [
    {{
      "step": 1,
      "description": "<specific instruction for the executor>",
      "action_type": "<analysis|reversing|symbolic|file_io|shell|crypto|web|data|artifacts|conversation>",
      "tool": "<specific_tool_name, skill:<skill-name>, or null for conversation>",
      "produces": "<artifact_key_or_null>"
    }}
  ]
}}

Example — "analyze /bin/ls and write a summary to notes.md":
{{
  "original_query": "analyze /bin/ls and write a summary to notes.md",
  "steps": [
    {{
      "step": 1,
      "description": "Identify the file type and architecture of /bin/ls",
      "action_type": "analysis",
      "tool": "file_info",
      "produces": null,
    }},
    {{
      "step": 2,
      "description": "Extract version info and printable strings from /bin/ls",
      "action_type": "analysis",
      "tool": "strings",
      "produces": null,
    }},
    {{
      "step": 3,
      "description": "Write a structured markdown summary of the analysis findings to notes.md",
      "action_type": "file_io",
      "tool": "write_file",
      "produces": null,
    }}
  ]
}}

Example — "find potential buffer overflow vulnerabilities in <target_file>":
{{
  "original_query": "find potential buffer overflow vulnerabilities in <target_file>",
  "steps": [
    {{
      "step": 1,
      "description": "Identify the file type and architecture of <target_file>",
      "action_type": "analysis",
      "tool": "file_info",
      "produces": null,
    }},
    {{
      "step": 2,
      "description": "Check security hardening features (NX, ASLR, stack canaries, PIE) on <target_file>",
      "action_type": "analysis",
      "tool": "checksec",
      "produces": null,
    }},
    {{
      "step": 3,
      "description": "Search for dangerous function calls (strcpy, gets, sprintf) in <target_file> strings",
      "action_type": "analysis",
      "tool": "strings",
      "produces": null,
    }},
    {{
      "step": 4,
      "description": "Decompile <target_file> to C pseudocode to examine function prologues and buffer handling",
      "action_type": "reversing",
      "tool": "ghidra_decompile",
      "produces": null,
    }},
    {{
      "step": 5,
      "description": "Extract symbol table to identify imported functions and potential attack surface",
      "action_type": "analysis",
      "tool": "nm",
      "produces": null,
    }}
  ]
}}

Example — "what's new in the LLM / AI agent / ML space" or "what techniques should I know about" or "what's trending in research":
{{
  "original_query": "what's new in the LLM space",
  "steps": [
    {{
      "step": 1,
      "description": "Get trending storyline clusters and hot topics from the Briefbot corpus — use window=3d to see what is rising right now",
      "action_type": "briefbot",
      "tool": "briefbot_trending",
      "produces": null,
    }},
    {{
      "step": 2,
      "description": "Search Briefbot corpus for recent papers and research in the top trending areas identified in step 1, ordered by date, limited to last 14 days",
      "action_type": "briefbot",
      "tool": "briefbot_search",
      "produces": null,
    }},
    {{
      "step": 3,
      "description": "Search Briefbot corpus for recent applied/industry developments (category=ai_industry) to complement the research findings",
      "action_type": "briefbot",
      "tool": "briefbot_search",
      "produces": null,
    }},
    {{
      "step": 4,
      "description": "Fetch full details on the 2-3 highest-signal items from the search results to get opportunity analysis and tags",
      "action_type": "briefbot",
      "tool": "briefbot_item",
      "produces": null,
    }}
  ]
}}

Example — "analyze _tests/proc and write a Python clone to _tests/run_5/proc_clone.py":
{{
  "original_query": "analyze _tests/proc and write a Python clone to _tests/run_5/proc_clone.py",
  "steps": [
    {{
      "step": 1,
      "description": "Run deep analysis and decompilation of _tests/proc to understand its algorithm, key derivation, padding, mode of operation, and CLI interface. Write the reconstructed Python clone to _tests/run_5/proc_clone.py.",
      "action_type": "reversing",
      "tool": "skill:deep-disassembly",
      "produces": null,
    }},
    {{
      "step": 2,
      "description": "Iteratively test _tests/run_5/proc_clone.py against the original _tests/proc binary using diff_behavior until all outputs match.",
      "action_type": "shell",
      "tool": "skill:test-reconstruction",
      "produces": null,
    }}
  ]
}}

Example — "summarize this paper: https://arxiv.org/abs/2604.21928":
{{
  "original_query": "summarize this paper: https://arxiv.org/abs/2604.21928",
  "steps": [
    {{
      "step": 1,
      "description": "Fetch the page at https://arxiv.org/abs/2604.21928 and store it as artifact key paper_content",
      "action_type": "web",
      "tool": "read_url",
      "produces": "paper_content",
    }},
    {{
      "step": 2,
      "description": "Read artifact key paper_content",
      "action_type": "artifacts",
      "tool": "get_artifact",
      "produces": null,
    }},
    {{
      "step": 3,
      "description": "Summarize the paper's main topic, goals, and findings based on the content",
      "action_type": "conversation",
      "tool": null,
      "produces": null,
    }}
  ]
}}

{context_block}Task: {user_message}\
"""

SYNTHESIS_SYSTEM_PROMPT = """\
You are a helpful assistant. Work was completed on the user's behalf and you \
are given a summary of what was accomplished. Respond naturally and \
conversationally — as if you did the work yourself.

Do not mention step numbers, tool names, or internal process details unless \
they are directly useful to the user. If something failed, acknowledge it \
briefly only if it affects the outcome the user asked for.

IMPORTANT — accuracy rules:
- Only assert specific technical details (implementation specifics, code behavior, \
algorithm details) that were explicitly confirmed by tool output during this session. \
If you are uncertain about a specific detail, omit it or hedge clearly.
- Do NOT fill gaps with training-data knowledge. If a source was not fetched, do not \
cite it. If a technique was not returned by a tool, do not invent it. Only report \
what the tools actually returned.
- If a file was already written to disk, do NOT reprint its full contents in your \
response — refer to the file path instead. Only quote short relevant excerpts if \
the user specifically needs to see them.

RESEARCH AND TREND QUERIES — when the user asked what's new, trending, or worth \
knowing about in a technical domain, structure your response around SIGNAL STRENGTH, \
not just content:
- Lead with what is RISING (high velocity, high momentum) — not just what exists.
- For each finding, explain WHY it is notable: what problem does it solve, what makes \
it different from prior work, why is it gaining traction now.
- Distinguish between a paper that just dropped and a technique that is being widely \
adopted — the signal is different. Use velocity and trend data from the tool results \
to make this distinction concrete.
- If the corpus returned trend scores or velocity numbers, use them to rank your \
response — the highest-signal items first.
- Do not pad the response with background knowledge about the field. Stay grounded \
in what the tools returned.\
"""

SYNTHESIS_USER_TURN = """\
Original request: {original_query}

What was accomplished:
{summary}

Respond to the user.\
"""
