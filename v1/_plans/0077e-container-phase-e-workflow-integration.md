# 0077e — Workflow Integration

## Goal
Wire the container toolset into the RE workflows:
1. Replace the bash round-trip in `deep-disassembly` step 11 with `diff_behavior`
2. Add a `test-reconstruction` workflow for standalone "iterate and fix" sessions
3. Generate and persist `test_spec.json` alongside reconstructed source files

---

## Change 1: `deep-disassembly` step 11

Current step 11 (bash round-trip):
```python
Step 11 [shell] bash_exec: compile with cc, encrypt with original,
decrypt with clone, diff — brittle, binary output breaks tool
```

New step 11 (when Docker available):
```python
Step(
    step=11,
    description=(
        f"Verify the reconstruction using diff_behavior:\n"
        f"  oracle_path: {target}\n"
        f"  oracle_type: native_binary\n"
        f"  candidate_path: {output}\n"
        f"  candidate_type: c_source\n"
        f"  test_cases: load from {test_spec_path} if it exists, otherwise use defaults\n\n"
        f"Read the DiffReport carefully:\n"
        f"  - build_error non-null → fix compile errors in {output} first\n"
        f"  - all_match=true → reconstruction is verified, report success\n"
        f"  - diverging cases → identify the bug pattern from mismatch_summary:\n"
        f"      * output length differs → padding calculation wrong\n"
        f"      * single-block cases match but multi-block diverge → CBC/IV missing\n"
        f"      * all cases diverge from byte 0 → key derivation wrong\n"
        f"    Then edit {output} to fix the identified bug and call diff_behavior again.\n"
        f"    Repeat until all_match=true. Do not give up after one failure."
    ),
    action_type=ActionType.SHELL,
    tool="diff_behavior",
    flags=StepFlags(),
)
```

The workflow checks `bool(settings.ghidra_home)` for Ghidra availability;
it will similarly check `container_available()` — if Docker is not running,
fall back to the existing bash round-trip step.

---

## Change 2: Test spec generation in step 9 (synthesis)

After identifying the CLI interface (from strings output) and algorithm constants,
step 9 generates a `test_spec.json` file alongside the C output:

```python
Step(
    step=9,
    description=(
        f"Reconstruct C source and generate test cases:\n"
        f"1. [existing synthesis instructions...]\n"
        f"2. After writing {output}, also write {test_spec_path} containing "
        f"a JSON array of test cases derived from your analysis:\n"
        f"   - Include boundary sizes: 1, 7, 8, 9, 15, 16 bytes of plaintext\n"
        f"   - Include passphrases of different lengths (2, 4, 8, 16 chars)\n"
        f"   - Use the CLI format confirmed from strings output\n"
        f"   Format: [{{\"id\": \"enc_1byte\", \"args\": [\"-e\", \"pass\", \"a\"]}}, ...]\n"
    ),
    ...
)
```

`test_spec_path = output.replace(".c", "_test_spec.json")`

This spec is reused across all subsequent `diff_behavior` calls for this target,
including standalone "iterate on the code" sessions.

---

## Change 3: New `test-reconstruction` workflow

**File:** `src/workflows/implementations/test_reconstruction.py`

**Intent:**
```python
intent = (
    "Use this workflow when the user wants to test, verify, or iterate on a "
    "reconstructed source file against an original binary. Triggers on phrases like "
    "'iterate on the code', 'test the clone against', 'verify the reconstruction', "
    "'does proc_clone match the original', 'check if my C file matches the binary'."
)
```

**Pattern:**
```python
pattern = re.compile(
    r"iterate\s+on\s+(?:the\s+)?(?:code|source|clone|reconstruction)"
    r"|test\s+(?:the\s+)?(?:clone|reconstruction|source)\s+against"
    r"|verify\s+(?:the\s+)?reconstruction"
    r"|does\s+\S+\s+match\s+(?:the\s+)?(?:original|binary)"
    r"|check\s+if\s+\S+\s+matches",
    re.IGNORECASE,
)
```

**Generated plan (5 steps):**

```
Step 1 [analysis]: Identify oracle path and candidate path from the user's message
  and conversation context. If not specified, look for recent .c files in _tests/
  and binaries matching their basename.

Step 2 [file_io]: Load test_spec.json if it exists alongside the candidate.
  If not found, generate a default battery from the CLI usage string (run
  run_target with args=[] to get usage, then infer test cases from the interface).

Step 3 [shell] diff_behavior: Run oracle vs candidate against all test cases.
  Read DiffReport.

Step 4 [conversation]: Analyze the DiffReport.
  - all_match=true → done
  - build_error → fix source, go to step 3
  - diverging cases → identify bug pattern, fix source, go to step 3
  Max 8 iterations (replanning loop).

Step 5 [conversation]: Synthesize — report final status: verified / partial / failed.
  If verified: "All N test cases pass. The reconstruction is behaviorally equivalent
  to the original binary."
  If not: summarize remaining divergences and what was attempted.
```

---

## Routing description

Add to `config.yml` toolset descriptions:
```yaml
toolset_descriptions:
  ...
  container: >-
    differential testing docker container run binary oracle candidate compare behavior
    verify reconstruction iterate fix round-trip test fuzz edge cases compile source
    native binary java jar python dynamic analysis behavioral equivalence
```

Add to static router rules (in `routing.static_router` or equivalent):
```python
# "iterate on" → plan, hint=test-reconstruction
# "verify the reconstruction" → plan, hint=test-reconstruction
# "test the clone" → plan, hint=test-reconstruction
```

---

## Fallback behavior when Docker is unavailable

Both `deep-disassembly` and `test-reconstruction` check `container_available()`
at plan generation time:

```python
if container_available():
    step_11 = diff_behavior_step(...)
else:
    step_11 = bash_roundtrip_step(...)  # existing fallback
```

The fallback is the existing bash round-trip. It's worse but still runs.
The agent log will note: "container toolset unavailable — using bash fallback for verification."

---

## File layout after all phases

```
src/tools/implementations/container/
  __init__.py
  runtime.py          # ContainerSession, ContainerLimits, VolumeMount, ContainerResult
  adapters.py         # TargetAdapter, NativeBinaryAdapter, TestCase, ADAPTER_REGISTRY
  tools.py            # run_target, diff_behavior, fuzz_target
  toolset.py          # CONTAINER_TOOLS, container_toolset

src/workflows/implementations/
  test_reconstruction.py   # new workflow

_tests/run_N/
  proc_clone.c             # reconstructed source
  proc_clone_test_spec.json   # generated test cases
```
