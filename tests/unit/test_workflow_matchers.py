"""Unit tests for workflow regex patterns."""
import pytest
from workflows.implementations.read_modify_write import ReadModifyWrite
from workflows.implementations.hash_and_report import HashAndReport
from workflows.implementations.analyze_and_write import AnalyzeAndWrite
from workflows.implementations.deep_disassembly import DeepDisassembly


# ── ReadModifyWrite ───────────────────────────────────────────────────

def test_read_modify_write_matches():
    wf = ReadModifyWrite()
    plan = wf.try_match("read config.json, modify the timeout field, write to config_new.json")
    assert plan is not None
    assert len(plan.steps) == 2


def test_read_modify_write_no_match_on_simple_read():
    wf = ReadModifyWrite()
    assert wf.try_match("read config.json and tell me what's in it") is None


# ── HashAndReport ─────────────────────────────────────────────────────

def test_hash_and_report_matches_hash():
    wf = HashAndReport()
    plan = wf.try_match("hash /bin/ls")
    assert plan is not None


def test_hash_and_report_matches_checksum():
    wf = HashAndReport()
    plan = wf.try_match("checksum the binary at /usr/bin/python3")
    assert plan is not None


def test_hash_and_report_no_match_random():
    wf = HashAndReport()
    assert wf.try_match("what is the capital of France") is None


# ── AnalyzeAndWrite ───────────────────────────────────────────────────

def test_analyze_and_write_matches():
    wf = AnalyzeAndWrite()
    plan = wf.try_match("analyze /bin/ls and write a summary to report.md")
    assert plan is not None


def test_analyze_and_write_no_match_without_write():
    wf = AnalyzeAndWrite()
    assert wf.try_match("analyze /bin/ls") is None


# ── DeepDisassembly ───────────────────────────────────────────────────

def test_deep_disassembly_matches_disassemble():
    wf = DeepDisassembly()
    plan = wf.try_match("disassemble /bin/ls")
    assert plan is not None


def test_deep_disassembly_matches_reverse_engineer():
    wf = DeepDisassembly()
    plan = wf.try_match("reverse engineer the binary at /tmp/target")
    assert plan is not None


def test_deep_disassembly_no_match_on_text():
    wf = DeepDisassembly()
    assert wf.try_match("read my notes.txt file") is None
