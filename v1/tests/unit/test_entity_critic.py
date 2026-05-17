"""Unit tests for entity critic path-correction logic."""
import pytest
from runtime.stages.entity_critic import _is_suspicious_candidate


# ── _is_suspicious_candidate ─────────────────────────────────────────

@pytest.mark.parametrize("old,new,expect_suspicious", [
    # bare word — no slash → suspicious
    ("target.bin", "binary", True),
    # very short → suspicious
    ("target.bin", "bi", True),
    # prose phrase with slash but no extension, no path markers → suspicious
    ("target.bin", "communication/rendering", True),
    # real path with extension → not suspicious
    ("foo.bin", "/tmp/target.elf", False),
    # relative path with ./ prefix → not suspicious
    ("old.txt", "./src/output.txt", False),
    # path starting with _ (project dir) → not suspicious
    ("foo", "_store/data/out.parquet", False),
    # old has extension, new does not → suspicious (path→dir substitution)
    ("/tmp/foo.asm", "/Users/user/agent-runtime", True),
    # both have no extension but new starts with / → not suspicious
    ("old", "/tmp/workdir", False),
    # ── All-caps crypto constants must never be corrected ────────────────
    # BLOCK/ROUNDS is a crypto constant pair, not a path
    ("BLOCK/ROUNDS", "_tests/run_6/proc-analysis.md", True),
    # ECB/CBC/CTR are mode names, not paths
    ("ECB/CBC", "_tests/output.md", True),
    # IV/KEY are crypto names, not paths
    ("IV/KEY", "/tmp/result.txt", True),
    # ── Prose slash-phrases must never be corrected ───────────────────────
    # XOR/shift/add — assembly operation description, not a path
    ("XOR/shift/add", "_tests/run_6/proc-analysis.md", True),
    # padding/unpadding — crypto description, not a path
    ("padding/unpadding", "_tests/run_6/proc-analysis.md", True),
    # encoding/decoding — description phrase, not a path
    ("encoding/decoding", "_tests/run_6/proc-analysis.md", True),
    # communication/rendering — prose, not a path
    ("communication/rendering", "_tests/output.md", True),
    # ── Real path-to-path corrections should still work ──────────────────
    # _tests/proc is a real path and should be correctable
    ("_tests/proc", "_tests/run_6/proc-analysis.md", False),
    # src/old.py → src/new.py — both look like paths
    ("src/old.py", "src/new.py", False),
])
def test_is_suspicious_candidate(old, new, expect_suspicious):
    assert _is_suspicious_candidate(old, new) is expect_suspicious
