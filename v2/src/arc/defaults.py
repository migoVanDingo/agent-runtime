"""Default config.yml content.

Kept as a string constant rather than a packaged file so there's one canonical
location. Bootstrap writes this verbatim when no config exists. The contents
match the spec in _design/0001-foundation-phase0-design.md §8.1.
"""

DEFAULT_CONFIG_YAML = """\
# ── Runtime ─────────────────────────────────────────────────────────────────
runtime:
  workspace: "."                    # working directory the agent operates in
  max_iterations: 50                # ReAct loop cap per turn
  max_tool_calls_per_turn: 30       # safety cap; agent forced to wrap up after this
  show_thinking: true               # render <thinking> blocks in TUI
  log_level: "info"                 # debug | info | warn | error
  # The base system prompt. Plugins can extend via before_llm_call.
  system_prompt: |
    You are arc, a careful, concise agent.

    Use only the tools you have actually been given in this session. Do
    not claim capabilities you weren't granted — when asked what you can
    do, refer to your actual tool list, never an idealized one.

    Don't fabricate. If a tool returns an error, change your approach:
    fix the input, try a different tool, or tell the user what you
    cannot do. NEVER repeat the same failed call with the same input —
    it will fail the same way.

    When you have the answer, give it directly without filler.
  # Injected when the runtime forces wrap-up at caps. The model sees this
  # as the next user turn instead of executing further tool calls.
  iteration_cap_message: |
    You have reached the maximum number of iterations for this turn. Stop
    calling tools. Summarize what you accomplished and what (if anything)
    remains undone.
  tool_call_cap_message: |
    You have reached the maximum number of tool calls for this turn. Stop
    calling tools. Summarize what you accomplished with the data you have.
  # Cycle detection: if the same tool is called N times consecutively with
  # the same input (and same kind of result), the agent is in a loop. The
  # runtime injects this message and forces a tool-less synthesis turn.
  cycle_detection_threshold: 3
  cycle_detected_message: |
    The same tool was just called several times in a row with identical
    input and the same kind of result. This is a cycle and you must
    stop. Do not call any more tools this turn. Summarize what you have
    learned, explain what you were trying to do, and tell the user
    clearly what you cannot complete.

# ── Provider ────────────────────────────────────────────────────────────────
provider:
  name: gemini                      # only "gemini" is exercised in v2.0–v2.2
  model: gemini-3.1-flash-lite-preview
  api_key_env: GEMINI_API_KEY       # env var name to read the key from
  base_url: null                    # null = SDK/library default
  timeout_seconds: 60
  retry:
    max_attempts: 3
    backoff_base_seconds: 2
    backoff_max_seconds: 32
  params:
    temperature: 0
    max_tokens: 4096
    top_p: 1.0

# ── Tools ───────────────────────────────────────────────────────────────────
tools:
  enabled: [ls, bash_exec]          # explicit list; unknown names cause startup error
  config:
    ls:
      max_depth: 2
      show_hidden: false
    bash_exec:                      # added in v2.1
      timeout_seconds: 30
      max_output_chars: 50000
      working_directory: null       # null = inherit runtime.workspace

# ── Plugins ─────────────────────────────────────────────────────────────────
# Order specified per hook. Lower numbers run earlier in the chain.
plugins:
  # Plugin policy — how the runtime treats misbehaving plugins.
  failure_threshold: 3              # disable a plugin after N exceptions/session
  exception_message_max_chars: 500  # truncate exception messages in events at N chars
  enabled:
    - name: jsonl-recorder
      config: {}                    # uses ARC_HOME/sessions/ by default
      hooks_order:
        on_session_start: 10        # set up session dir + meta BEFORE events arrive
        on_event: 100               # append each event to events.jsonl
        on_session_end: 10          # stamp meta.json + append index.jsonl on exit
    - name: guard
      config:
        # Tools in this list bypass all guard checks. The runtime built-in
        # `ls` is safe by construction (read-only directory listing).
        allowlist_tools: [ls]
        # Regex patterns checked against the `command` input of any tool.
        # A match → hard deny via ToolDenial.
        blocklist_patterns:
          - 'rm\\s+-rf'
          - 'dd\\s+if='
          - ':\\(\\)\\s*\\{'                # fork bomb
          - '>\\s*/dev/(s|h|nv)d[a-z]'       # writing to block devices
          - 'mkfs'                           # formatting filesystems
          - 'shutdown'
          - 'reboot'
        # Regex patterns that require interactive approval.
        # `arc run` (headless) auto-denies these.
        escalation_required_patterns:
          - '\\bcurl\\b'
          - '\\bwget\\b'
          - '\\bnc\\b'                       # netcat
          - '\\bssh\\b'
          - '\\bscp\\b'
          - '\\bsudo\\b'
          - '\\bchmod\\b\\s+(?:\\+s|[0-7]*7[0-7][0-7])'  # setuid or world-write
      hooks_order:
        before_tool_call: 10
    - name: pause-resume
      config: {}                    # signal file is <session_dir>/pause
      hooks_order:
        pause_check: 50

# ── TUI ─────────────────────────────────────────────────────────────────────
tui:
  enabled: true                     # false = headless CLI mode (also used by replay)
  theme: default
  inline_mode: true                 # true = scrollback works; false = alt-screen (don't)
  spinner_style: dots
  prompt_prefix: "❯ "
  show_token_counts: true
  show_event_count: false           # debug aid; off by default

# ── Bootstrap defaults (used by `arc bootstrap`) ────────────────────────────
bootstrap:
  create_workspace_dir: false       # whether to create runtime.workspace if missing
  write_example_session: false      # whether to seed an example for replay testing
"""
