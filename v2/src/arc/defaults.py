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

    Prefer sub-agents for the work they own. Before doing a task yourself
    with low-level tools, check whether a `subagent_*` tool is described
    for exactly this kind of work (e.g. deploying/managing containers,
    analyzing video). If one fits, delegate to it FIRST with a clear task
    description — don't attempt the task with individual tools and fall
    back to the sub-agent only after failing. The sub-agent owns the
    methodology and verifies its own work.

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
# To switch to Anthropic, change the three lines below:
#   name: anthropic
#   model: claude-haiku-4-5    (or claude-sonnet-4-5, claude-opus-4-7, etc.)
#   api_key_env: ANTHROPIC_API_KEY
# Everything else (retry, params) works the same. The Anthropic SDK requires
# max_tokens — leave the default below or override in params.
#
# Local providers (no API costs, but require a running inference server):
#   Ollama:
#     name: ollama
#     model: llama3.1:8b              # any locally-pulled, tool-capable tag
#     api_key_env: OLLAMA_API_KEY     # ignored by stock Ollama; placeholder used
#     base_url: http://localhost:11434/v1
#     timeout_seconds: 120            # local inference is slower than cloud
#   llama.cpp (llama-server):
#     name: llama_cpp
#     model: ""                       # informational; llama-server has 1 model loaded
#     api_key_env: LLAMA_CPP_API_KEY  # honor --api-key on llama-server if set
#     base_url: http://localhost:8080/v1
#     timeout_seconds: 120
#     params:
#       mode: compat                  # 'compat' (OpenAI-compatible) | 'grammar' (GBNF)
provider:
  name: gemini                      # 'gemini' | 'anthropic' | 'ollama' | 'llama_cpp'
  model: gemini-2.5-flash
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
    # Don't set top_p here — Anthropic rejects `temperature` + `top_p` both
    # being specified ("temperature and top_p cannot both be specified").
    # If you want nucleus sampling, set ONE of them, not both.

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
        # Tool-name globs the MAIN session may not call directly — allowed
        # only inside the owning sub-agent's session. Forces orchestration
        # through a sub-agent that verifies before reporting. Maps glob ->
        # the sub-agent tool to redirect to. Empty by default.
        delegate_only_tools: {}
        #   container_*: subagent_container_expert
        #   network_*: subagent_container_expert
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
          # Force container work through the cos tools / container sub-agent
          # instead of a raw host shell (bash_exec). Applies in sub-agents too
          # now that children inherit the guard. Remove to allow raw docker.
          - '\\bdocker(-compose)?\\b'
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
        # Learns the final tool list so delegate_only_tools can fail open
        # when an owner sub-agent is absent. Only needed if you use that knob.
        on_event: 100
    - name: safety-gate
      # Destructive-action confirmation. Pattern-matches commands like
      # `rm <file>`, `git reset --hard`, `git push --force`, etc., and
      # prompts the user via UserGate before they execute. See
      # _design/0012-destructive-action-gate.md.
      #
      # Headless mode (`arc run`) uses NoOpGate which auto-denies — by
      # design. If you want headless to run destructive ops, set
      # bypass_mode: true here for that invocation.
      config:
        enabled: true
        bypass_mode: false
        enabled_patterns:
          - rm-file
          - rm-recursive
          - git-reset-hard
          - git-clean-force
          - git-push-force
          - truncate
          - chown-recursive
          - chmod-recursive
          - redirect-overwrite
          - drop-table
          - drop-database
          - truncate-sql
        custom_patterns: []         # [{name, description, regex}, ...]
      hooks_order:
        before_tool_call: 20        # AFTER guard (10); guard's hard denies win first
    - name: pause-resume
      config: {}                    # signal file is <session_dir>/pause
      hooks_order:
        pause_check: 50
    - name: log-writer
      config:
        level: info                 # debug | info | warn | error
        preview_chars: 200          # truncate long messages/outputs
        include_events: []          # if non-empty, ONLY log these event types
        exclude_events: []          # event types to skip
      hooks_order:
        # session_start FIRST so the file exists before the recorder
        # starts firing on_event. Same trick we use for jsonl-recorder.
        on_session_start: 5
        on_event: 50
        on_session_end: 5
    - name: timeline
      config:
        # Visual session timeline (0027). Regenerates sessions/timeline.html
        # + per-session session.html on session end. Open with `arc timeline`.
        summary_max_chars: 400        # per-field truncation in the forest view
        full_output_max_chars: 20000  # per tool output in session.html
      hooks_order:
        on_session_end: 60            # after the recorder stamps meta (10)
    - name: sliding-window-context
      config:
        keep_first_turns: 2         # always preserve original goals/setup
        keep_last_turns: 20         # always preserve recent reasoning
        max_tokens: null            # null = no token budget, only turn count
        token_estimate_chars_per: 4 # chars/N ≈ tokens, ~10-20% accurate
      hooks_order:
        pack_context: 100
    # MCP client bridge (0025). Consumes external MCP servers and surfaces their
    # tools into the registry. Empty `servers` = connects to nothing (no cost).
    # Add servers to integrate a standalone service (e.g. the container
    # orchestrator) or a third-party MCP server. Needs the `mcp` extra:
    # `pip install "arc[mcp]"`. Toggle whole-bridge here; per-server in `arc setup`.
    - name: mcp
      config:
        failure_threshold: 3        # per-server strikes before quarantine
        call_timeout_s: 30          # default per-tool-call timeout
        servers: []
        #   - name: container        # -> tools prefixed `container_`
        #     transport: http        # http | stdio
        #     url: http://127.0.0.1:8770/mcp
        #     enabled: true
        #     tool_prefix: container
        #     tools_allow: []        # empty = all; else allowlist of tool names
        #     tools_deny: []
        #   - name: proxmox
        #     transport: stdio
        #     command: ["uvx", "proxmox-mcp"]
        #     env: {}
        #     enabled: true
        #     tool_prefix: proxmox
      hooks_order:
        on_session_start: 8         # connect + discover before tools are merged
        on_session_end: 8           # disconnect cleanly

# ── Sub-agents (0020) ───────────────────────────────────────────────────────
# Optional. List installed sub-agent specs here to override fields (model,
# timeout_s, max_dispatches_per_session, etc.) or define new config-only
# specs. Built-ins and plugin-discovered specs are loaded automatically;
# you only need an entry here if you want to customize one.
#
# Example overrides on a plugin-shipped spec:
#   subagents:
#     example_log_grepper:
#       model: claude-haiku-4-5
#       timeout_s: 60
#       enabled: true
#
# Example new config-only spec:
#   subagents:
#     my_classifier:
#       description: "Classify a snippet as one of {A, B, C}. Return JSON."
#       provider: anthropic
#       model: claude-haiku-4-5
#       system_prompt: "You are a focused classifier. Return only JSON …"
#       tools: []
subagents: {}

# ── TUI ─────────────────────────────────────────────────────────────────────
tui:
  enabled: true                     # false = headless CLI mode (also used by replay)
  theme: default
  inline_mode: true                 # true = scrollback works; false = alt-screen (don't)
  spinner_style: dots
  prompt_prefix: "❯ "
  show_token_counts: true
  show_event_count: false           # debug aid; off by default
  # Render <thinking> blocks (Claude 3.7+ / 4+ extended thinking) in the TUI.
  # Always preserved in events.jsonl + session.log regardless of this flag.
  show_thinking: true
  # Collapse tool outputs longer than N lines into a one-line summary.
  # Full output is in events.jsonl + session.log.
  tool_output_max_lines: 30
  # Persistent bottom toolbar with provider/model/session/turn/tokens/$cost.
  toolbar_enabled: true
  # Up/down recalls past inputs (FileHistory in ARC_HOME/history).
  input_history_enabled: true
  # Stream a sub-agent's tool activity into the scrollback (nested lines)
  # instead of only a spinner, so you can watch what a sub-agent is doing.
  subagent_activity: true
  # Max open session tabs (0026 time travel: /rewind, /retry and /model
  # open the branch in a new tab). Each live tab holds its own plugins and
  # MCP connections — keep small.
  tabs_max: 4

# ── Bootstrap defaults (used by `arc bootstrap`) ────────────────────────────
bootstrap:
  create_workspace_dir: false       # whether to create runtime.workspace if missing
  write_example_session: false      # whether to seed an example for replay testing
"""


# ── catalog.yml — drives the `arc setup` picker (see 0017) ─────────────────

DEFAULT_CATALOG_YAML = """\
# arc model catalog — drives the `arc setup` picker.
# Add, remove, reorder entries freely.  The picker shows them top-to-bottom.
#
# Fields:
#   id     (required) — the model id written into config.yml on selection
#   label  (required) — display text in the picker
#   note   (optional) — trailing hint, shown dim-colored in the picker

anthropic:
  - id: claude-opus-4-7
    label: "Opus 4.7"
    note: "most capable, slowest"
  - id: claude-sonnet-4-6
    label: "Sonnet 4.6"
    note: "balanced; default recommendation"
  - id: claude-haiku-4-5
    label: "Haiku 4.5"
    note: "fastest, cheapest"

gemini:
  - id: gemini-2.5-pro
    label: "2.5 Pro"
    note: "most capable, stable"
  - id: gemini-2.5-flash
    label: "2.5 Flash"
    note: "balanced default, stable"
  - id: gemini-2.5-flash-lite
    label: "2.5 Flash-Lite"
    note: "cheapest stable"
  - id: gemini-3-pro-preview
    label: "3 Pro (preview)"
    note: "newest large model — preview, may change"
  - id: gemini-3-flash-preview
    label: "3 Flash (preview)"
    note: "newest fast model — preview, may change"

# Local providers — leave empty to use live discovery
# (Ollama /api/tags, llama.cpp /v1/models).  Pin defaults here only if
# you want them to appear in the picker even before the server is reachable.
ollama: []
llama_cpp: []
"""


# ── llm_servers.yml — drives `arc llm` lifecycle (see 0018) ────────────────

DEFAULT_LLM_SERVERS_YAML = """\
# arc llm-server registry — drives `arc llm` commands and the 0017 picker.
#
# Edit this file to point arc at your llama-server binary (or the
# llama-cpp-python bundled server, installable via `pip install
# llama-cpp-python[server]`) and to register the .gguf models you've
# downloaded.

binary:
  # Two options:
  #
  # A) User-compiled llama.cpp (faster, more knobs):
  #   path: ~/llama.cpp/build/bin/llama-server
  #   kind: llama_cpp
  #
  # B) llama-cpp-python's bundled server (no sudo required):
  path: python
  kind: llama_cpp_python              # uses `python -m llama_cpp.server`

# Args appended to every invocation.  Override per-model in `extra_args` below.
default_args:
  - "--host"
  - "127.0.0.1"
  - "--port"
  - "8080"

# How long to wait for /health to flip to "ok" after starting.
# Cold model load can take 60-180s on first call.
startup_timeout_seconds: 180

# Models you have downloaded.  Each entry becomes a pickable choice
# in `arc llm list` and the 0017 picker.
models: []
# Example:
# models:
#   - id: llama-3.1-8b
#     label: "Llama 3.1 8B Instruct (Q4)"
#     gguf: ~/models/llama-3.1-8b-instruct.Q4_K_M.gguf
#     extra_args: ["-c", "8192", "-ngl", "99"]
#   - id: qwen-2.5-coder-32b
#     label: "Qwen 2.5 Coder 32B (Q4)"
#     gguf: ~/models/qwen2.5-coder-32b-instruct.Q4_K_M.gguf
#     extra_args: ["-c", "16384", "-ngl", "99"]
"""
