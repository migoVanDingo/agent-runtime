# 0077c — Core Tools

## Goal
Implement the three tools the agent actually calls. Each tool uses the adapter layer
(phase B) and container runtime (phase A). Tools return structured dicts — no raw text.

## New File: `src/tools/implementations/container/tools.py`

---

### Tool: `run_target`

**When used:** Exploration. "How does this binary behave?" The agent runs the oracle
(or any single target) against test cases and folds the outputs into its analysis.
No comparison, no candidate.

**Signature:**
```python
def run_target(
    path: str,
    type: str,                         # TargetSpec.type
    test_cases: list[dict],            # serialized TestCase list
    image: str | None = None,
    build_flags: list[str] | None = None,
    timeout_seconds: float = 60.0,
) -> dict:
```

**Returns:**
```json
{
  "build_error": null,
  "cases": [
    {
      "id": "enc_hello",
      "args": ["-e", "pass", "helloworld"],
      "stdout": "1a2b3c4d5e6f7081a2b3c4d5e6f70812",
      "stderr": "",
      "exit_code": 0,
      "timed_out": false,
      "duration_ms": 45
    }
  ],
  "total_duration_ms": 312
}
```

**Container script (generated):**
```bash
cp /workspace/_tests/proc /tmp/target && chmod +x /tmp/target
echo '__RESULTS_START__'
# Per test case:
OUT=$( /tmp/target -e pass helloworld 2>/tmp/stderr_enc_hello )
ERR=$(cat /tmp/stderr_enc_hello)
EXIT=$?
echo '{"id":"enc_hello","stdout":"'$OUT'","stderr":"'$ERR'","exit_code":'$EXIT'}'
# ... repeat for each case
echo '__RESULTS_END__'
```

Output is JSON-per-line between sentinels, parsed from `ContainerResult.stdout`.

---

### Tool: `diff_behavior`

**When used:** Reconstruction loop. Compares oracle vs candidate on the same test cases.
Primary feedback mechanism for the fix loop.

**Signature:**
```python
def diff_behavior(
    oracle_path: str,
    oracle_type: str,
    candidate_path: str,
    candidate_type: str,
    test_cases: list[dict],
    oracle_image: str | None = None,
    candidate_image: str | None = None,
    candidate_build_flags: list[str] | None = None,
    timeout_seconds: float = 120.0,
) -> dict:
```

**Returns:**
```json
{
  "all_match": false,
  "total": 10,
  "matching": 4,
  "build_error": null,
  "cases": [
    {
      "id": "enc_hello",
      "oracle_stdout": "1a2b3c4d5e6f7081a2b3c4d5e6f70812",
      "candidate_stdout": "9f8e7d6c5b4a392817263544",
      "oracle_stderr": "",
      "candidate_stderr": "",
      "match": false,
      "mismatch_summary": "stdout differs: oracle=32 chars (16 bytes), candidate=24 chars (12 bytes)"
    },
    {
      "id": "enc_1byte",
      "oracle_stdout": "ab12cd34ef567890",
      "candidate_stdout": "ab12cd34ef567890",
      "oracle_stderr": "",
      "candidate_stderr": "",
      "match": true,
      "mismatch_summary": null
    }
  ],
  "total_duration_ms": 1840
}
```

**Container script structure:**
```bash
# Build candidate
{candidate_build_commands}   # from adapter.build_commands()

# Build oracle (copy only — always a pre-built binary)
cp {oracle_path} /tmp/oracle && chmod +x /tmp/oracle

echo '__RESULTS_START__'
for each test_case:
  ORACLE_OUT=$( {oracle_invoke} 2>/tmp/o_err )
  ORACLE_ERR=$(cat /tmp/o_err); ORACLE_EXIT=$?
  CAND_OUT=$( {candidate_invoke} 2>/tmp/c_err )
  CAND_ERR=$(cat /tmp/c_err); CAND_EXIT=$?
  # emit JSON line
echo '__RESULTS_END__'
```

**Mismatch summary logic (Python-side, after parsing):**
- Length differs → "stdout differs: oracle=N chars (M bytes), candidate=P chars (Q bytes)"
- Same length, content differs → "stdout differs at position N: oracle=0xXX, candidate=0xYY"
- Exit code differs → "exit_code differs: oracle=0, candidate=1"
- Stderr differs → "stderr differs"

---

### Tool: `fuzz_target`

**When used:** Edge case discovery. The agent doesn't want to specify test cases manually.
The tool generates them from a strategy and diffs.

**Signature:**
```python
def fuzz_target(
    oracle_path: str,
    oracle_type: str,
    candidate_path: str,
    candidate_type: str,
    strategy: str,                     # "boundary" | "random" | "mutation"
    arg_template: list[str],           # e.g. ["-e", "{passphrase}", "{data}"]
    n_cases: int = 50,
    seed_cases: list[dict] | None = None,
    timeout_seconds: float = 120.0,
) -> dict:
```

**Strategies:**
- `"boundary"` — generates inputs at block boundaries (1, 7, 8, 9, 15, 16, 17 bytes, etc.)
  Uses `arg_template` with `{data}` substituted. Auto-detects block size if not given.
- `"random"` — random printable strings of random lengths (1–256 bytes)
- `"mutation"` — starts from `seed_cases`, mutates args (flip bytes, truncate, extend)

Returns same `DiffReport` format as `diff_behavior`, with `generated_cases` added:
```json
{
  "all_match": false,
  "generated_cases": [...],   // the cases that were generated
  "cases": [...],
  ...
}
```

---

## Shared internal: `_run_diff_container`

Both `diff_behavior` and `fuzz_target` call a shared internal function that:
1. Resolves adapters for oracle and candidate
2. Generates the container script
3. Calls `ContainerSession.run()`
4. Parses JSON lines from output
5. Computes mismatch summaries
6. Returns `DiffReport`

This avoids duplication between the two tools.

---

## Error handling

| Error | Behavior |
|---|---|
| Docker not available | Tool returns `{"error": "docker not available", "available": false}` |
| Build fails (compile error) | `{"build_error": "<compiler output>", "cases": []}` |
| Container timeout | `{"timed_out": true, "cases": [...partial...]}` |
| Individual case timeout | Case marked `"timed_out": true`, test continues |
| Binary crashes (non-zero exit) | Captured in `exit_code`, not treated as error — just a divergence |

The agent reads `build_error` and `timed_out` fields explicitly. The synthesis step in
`deep-disassembly` prompts the agent to check these fields before interpreting results.
