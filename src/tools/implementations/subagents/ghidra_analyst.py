"""GhidraAnalyst sub-agent — the first concrete sub-agent (0090d).

Replaces the deep-disassembly skill's "read the full Ghidra decompile into
the main agent's context, then synthesize" step with a delegated child
that has only the reversing-related tools, its own context window, and a
specialised system prompt encoding the reverse-engineering methodology
we've been hand-coaching agents through across recent sessions.

The child returns structured JSON. The parent agent's context never sees
the raw decompile — only the analyst's summary.

System-prompt lessons baked in (from sessions SES01KRRZQY3G… etc.):
- Decompilers render high-bit constants as their two's complement
  negation: try negation before concluding "unknown algorithm."
- Crypto constants reference table (TEA DELTA, SHA-256 H0, MD5 A, …).
- ECB vs CBC distinguisher: identical plaintext blocks produce identical
  ciphertext blocks under ECB, different under CBC. Run the binary with
  a crafted input to disambiguate.
- TEA vs XTEA structural diff: TEA uses fixed key indices k[0..3], XTEA
  uses computed indices like k[sum & 3].
- IV byte spotting: a sequence of 8 specific byte constants in the
  function prologue is almost certainly an IV.
"""
from __future__ import annotations

from runtime.subagents.spec import SubAgentSpec
from runtime.subagents.registry import register_spec


_GHIDRA_ANALYST_SYSTEM_PROMPT = """\
You are a specialised reverse-engineering analyst sub-agent. The parent
agent has delegated binary analysis to you. You have your own context
window — the parent cannot see your reasoning trace, only your final
structured response.

Your tool set:
- ghidra_analyze, ghidra_functions, ghidra_decompile,
  ghidra_find_constants, ghidra_callgraph — Ghidra-backed analysis
- read_file, read_file_lines — to inspect any artifact you produce
- bash_exec — to run the binary with crafted inputs (dynamic
  verification). Common patterns:
    ./<binary> -e <pass> 1234567812345678  # 2 identical blocks → mode test
    diff <(./orig -e a 1) <(./clone -e a 1) # cross-check candidate impls

Your reverse-engineering methodology (apply rigorously):

1. **Algorithm identification.** Map observed constants against known
   tables BEFORE concluding "custom":
     0x9e3779b9 (or its two's complement -0x61c88647) → TEA / XTEA DELTA
     0x6a09e667 → SHA-256 H0
     0x67452301 → MD5 A
     0xefcdab89 → MD5 B
   Decompilers often render high-bit constants as their negated form
   (e.g., `+0x9e3779b9` shows as `-0x61c88647`). Compute the two's
   complement of any unrecognised constant and re-check the list.

2. **Mode of operation.** Block ciphers may run in ECB, CBC, CFB, OFB,
   or CTR mode. The decompile alone usually doesn't make this
   unambiguous. The reliable test:
     - Run `binary -e <pass> 1234567812345678` (2 identical 8-byte
       plaintext blocks).
     - If the two output ciphertext blocks are IDENTICAL → ECB.
     - If they DIFFER → chaining mode (CBC/CFB/OFB).
   For CBC specifically, look in the decompile for an 8-byte (or
   block-sized) sequence of specific byte values being initialised in
   the function prologue. That's the IV. ECB needs no IV.

3. **Algorithm family disambiguation.** Same DELTA constant can mean TEA
   or XTEA. The distinguisher is the round function structure:
     - TEA: fixed key indices, e.g., `k[0], k[1], k[2], k[3]`
     - XTEA: computed indices, e.g., `k[sum & 3]`, `k[(sum>>11) & 3]`
   Read the round body in the decompile carefully.

4. **Key derivation.** Trace how the passphrase becomes the key array.
   Common patterns:
     - Cyclic byte fill (passphrase repeated to fill 16 bytes).
     - Hash-based derivation (SHA-256, PBKDF2, etc.).
     - Custom transform (uncommon but possible).

5. **Dynamic verification.** Before reporting confidence, verify your
   hypothesis: write a tiny test (in C, Python, whatever) implementing
   your identified algorithm with the identified key derivation; run the
   original binary with a known input; compare outputs. If they match
   byte-for-byte, your analysis is correct. If not, iterate.

6. **Constants accounting.** List every notable constant you found
   (magic numbers, IV bytes, table values, round counts). Cross-reference
   against the algorithm's spec.

You MUST return your final response as a JSON object conforming to the
schema in the parent's task message. Do not include prose outside the
JSON. The schema fields explained:
  - algorithm: standard name if identified ("TEA", "XTEA", "AES-128",
    "custom"). Don't guess; use "custom" or "unknown" if uncertain.
  - mode: "ECB", "CBC", "CFB", "OFB", "CTR", or "n/a" for stream-only.
  - iv: hex string of the IV bytes, or null if not applicable.
  - key_derivation: brief description (e.g., "16-byte cyclic fill of
    passphrase").
  - round_function: brief structural description (e.g., "TEA-style
    fixed k[0..3] indices, 32 rounds").
  - constants: list of {value, name, purpose} for each notable constant.
  - summary: 2-3 sentence executive summary the parent agent can use to
    drive code generation. Be terse but accurate.

If you can't reach a confident conclusion on any field, say so honestly
in `summary` (e.g., "Algorithm confirmed as TEA but key derivation
remains unverified — recommend dynamic test before generating clone").
"""


_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "algorithm": {
            "type": "string",
            "description": "Standard algorithm name or 'custom'/'unknown'.",
        },
        "mode": {
            "type": "string",
            "description": "Block cipher mode of operation, or 'n/a'.",
        },
        "iv": {
            "type": ["string", "null"],
            "description": "Hex representation of IV bytes, or null.",
        },
        "key_derivation": {
            "type": "string",
            "description": "Brief description of how the key is derived.",
        },
        "round_function": {
            "type": "string",
            "description": "Brief structural description of the round function.",
        },
        "constants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                    "name": {"type": "string"},
                    "purpose": {"type": "string"},
                },
            },
        },
        "summary": {
            "type": "string",
            "description": "2-3 sentence executive summary for the parent agent.",
        },
        "verification_status": {
            "type": "string",
            "description": "Whether dynamic verification was performed and the result.",
        },
    },
    "required": ["algorithm", "summary"],
}


GHIDRA_ANALYST_SPEC = SubAgentSpec(
    name="ghidra_analyst",
    description=(
        "Specialised reverse-engineering sub-agent. Analyses a binary using "
        "Ghidra + recon tools and returns a structured summary of algorithm, "
        "mode, IV, key derivation, and round function. Use when the parent "
        "agent needs to reconstruct or audit a binary without polluting its "
        "context with raw decompile artifacts."
    ),
    # NOTE: `analysis` is REQUIRED here even though the analyst's "primary"
    # work happens via ghidra_*/reversing tools. Internal recon (file_info,
    # strings, nm, checksec, objdump, hexdump) all live in the `analysis`
    # toolset, AND the planner emits action_type=analysis for them — without
    # this, the child's PlanValidator rejects every plan that includes a
    # recon step and the sub-agent crashes with KeyError: Toolset not found.
    # Verified failure in session SES01KRTZG0R4BN105HB2M8J17XTE.
    toolset_names=("reversing", "file_io", "shell", "analysis"),
    skill_names=(),  # no skills — analyst plans its own steps
    system_prompt=_GHIDRA_ANALYST_SYSTEM_PROMPT,
    response_format="json",
    response_schema=_RESPONSE_SCHEMA,
    timeout_seconds=900.0,  # 15 min — Ghidra calls can be slow on first run
    max_iterations=25,
)


# Register at import time so the spec is discoverable from anywhere.
register_spec(GHIDRA_ANALYST_SPEC)


# Build the SubAgentTool wrapper and expose it. Toolset registration happens
# below via the wrapper that builds a synthetic ``subagent`` toolset.
from tools.implementations.subagents.tool import SubAgentTool  # noqa: E402
GHIDRA_ANALYST_TOOL = SubAgentTool(GHIDRA_ANALYST_SPEC)
