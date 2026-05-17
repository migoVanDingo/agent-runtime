#!/usr/bin/env python3
"""Analyze agent log files to discover common task patterns.

Reads all .log files in _logs/, extracts user messages and plan steps,
and reports the most common tool sequences and plan patterns.

Usage:
    python scripts/analyze_logs.py
"""

import re
import sys
from pathlib import Path
from collections import Counter

LOGS_DIR = Path(__file__).resolve().parent.parent / "_logs"


def extract_sessions(log_path: Path) -> list[dict]:
    """Extract user messages and plan steps from a log file."""
    sessions = []
    current = {"user_messages": [], "tool_sequences": [], "steps": []}

    with open(log_path) as f:
        for line in f:
            line = line.strip()

            # User messages
            user_match = re.search(r"agent:\s+(\S.+)", line)
            if "── User ──" in line:
                continue
            if "agent:" in line and not any(x in line for x in ["──", "Step", "tool", "monitor", "critic", "runtime"]):
                msg = re.sub(r"^\d{4}-\d{2}-\d{2}\s+\S+\s+\[INFO\]\s+agent:\s+", "", line)
                if msg and len(msg) > 2:
                    current["user_messages"].append(msg.strip())

            # Plan steps with tools
            step_match = re.search(r"Step \d+ \[(\w+)\] tool=(\w+):", line)
            if step_match:
                action_type = step_match.group(1)
                tool = step_match.group(2)
                current["steps"].append({"action_type": action_type, "tool": tool})

            # Tool calls (direct mode)
            tool_call_match = re.search(r"→ (\w+)\s", line)
            if tool_call_match:
                current["tool_sequences"].append(tool_call_match.group(1))

            # Session end
            if "Session Ended" in line:
                if current["steps"] or current["tool_sequences"]:
                    sessions.append(current)
                current = {"user_messages": [], "tool_sequences": [], "steps": []}

    # Don't forget the last session if it didn't end cleanly
    if current["steps"] or current["tool_sequences"]:
        sessions.append(current)

    return sessions


def main():
    if not LOGS_DIR.exists():
        print(f"No logs directory found at {LOGS_DIR}")
        sys.exit(1)

    log_files = sorted(LOGS_DIR.glob("*.log"))
    if not log_files:
        print("No log files found")
        sys.exit(1)

    print(f"Analyzing {len(log_files)} log file(s)...\n")

    all_sessions = []
    for log_path in log_files:
        sessions = extract_sessions(log_path)
        all_sessions.extend(sessions)

    print(f"Found {len(all_sessions)} session(s) with tool usage\n")

    # Analyze plan tool sequences
    plan_sequences = Counter()
    tool_usage = Counter()
    action_type_usage = Counter()

    for session in all_sessions:
        if session["steps"]:
            seq = tuple(s["tool"] for s in session["steps"])
            plan_sequences[seq] += 1
            for s in session["steps"]:
                tool_usage[s["tool"]] += 1
                action_type_usage[s["action_type"]] += 1

        for tool in session["tool_sequences"]:
            tool_usage[tool] += 1

    print("=" * 60)
    print("  TOOL USAGE (most common)")
    print("=" * 60)
    for tool, count in tool_usage.most_common(20):
        print(f"  {tool:30s} {count:4d}")

    print()
    print("=" * 60)
    print("  ACTION TYPE USAGE")
    print("=" * 60)
    for at, count in action_type_usage.most_common():
        print(f"  {at:30s} {count:4d}")

    print()
    print("=" * 60)
    print("  PLAN TOOL SEQUENCES (most common)")
    print("=" * 60)
    for seq, count in plan_sequences.most_common(10):
        print(f"  {' → '.join(seq):50s} {count:4d}")

    print()
    print("=" * 60)
    print("  PLAN LENGTHS")
    print("=" * 60)
    lengths = Counter()
    for session in all_sessions:
        if session["steps"]:
            lengths[len(session["steps"])] += 1
    for length, count in sorted(lengths.items()):
        print(f"  {length} steps: {count} plan(s)")


if __name__ == "__main__":
    main()
