"""Byte-fidelity probe for the anthropic SDK — mirror of the Gemini probe.

Run: python _tests/experiment_anthropic_sdk_fidelity.py

Verifies which serialization path on Anthropic's response object gives us
a JSON-round-trippable view we can store in events.jsonl for replay.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _load_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _probe() -> None:
    _load_env()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("SKIP: ANTHROPIC_API_KEY not set")
        return

    from anthropic import Anthropic

    model = os.environ.get("ARC_TEST_MODEL", "claude-haiku-4-5")
    client = Anthropic(api_key=api_key)

    print(f"Calling model: {model}")
    print("---")

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=128,
            messages=[{"role": "user", "content": "Say only the word 'pong'."}],
        )
    except Exception as e:
        print(f"Call failed: {type(e).__name__}: {e}")
        print("\nTry: ARC_TEST_MODEL=claude-3-5-haiku-latest")
        sys.exit(1)

    # Extract text
    text = ""
    for block in resp.content:
        if hasattr(block, "text"):
            text = block.text
            break
    print(f"Response text: {text!r}")
    print(f"Response type: {type(resp).__name__}")
    print(f"Has .model_dump()? {hasattr(resp, 'model_dump')}")
    print(f"Has .model_dump_json()? {hasattr(resp, 'model_dump_json')}")
    print(f"Has .to_dict()? {hasattr(resp, 'to_dict')}")

    findings = []

    if hasattr(resp, "model_dump"):
        try:
            d = resp.model_dump(mode="json", exclude_none=False)
            roundtripped = json.loads(json.dumps(d, default=str))
            findings.append(("model_dump(mode='json')", True, len(json.dumps(d)), roundtripped))
            print(f"\n✓ model_dump(mode='json') round-trips ({len(json.dumps(d))} bytes)")
        except Exception as e:
            findings.append(("model_dump(mode='json')", False, 0, str(e)))
            print(f"\n✗ model_dump(mode='json') failed: {e}")

    if hasattr(resp, "model_dump_json"):
        try:
            raw = resp.model_dump_json()
            roundtripped = json.loads(raw)
            findings.append(("model_dump_json()", True, len(raw), roundtripped))
            print(f"✓ model_dump_json() round-trips ({len(raw)} bytes)")
        except Exception as e:
            findings.append(("model_dump_json()", False, 0, str(e)))
            print(f"✗ model_dump_json() failed: {e}")

    print(f"\nUsage:")
    if hasattr(resp, "usage") and resp.usage:
        u = resp.usage
        print(f"  input_tokens:  {getattr(u, 'input_tokens', '?')}")
        print(f"  output_tokens: {getattr(u, 'output_tokens', '?')}")

    print(f"Stop reason: {resp.stop_reason}")

    successful = [f for f in findings if f[1]]
    print("\n" + "=" * 60)
    if successful:
        best = min(successful, key=lambda f: -f[2])
        print(f"VERDICT: SDK is byte-faithful via .{best[0]}")
        print(f"         Strategy: store the dict in events.jsonl content field")
    else:
        print("VERDICT: SDK is NOT byte-faithful; wrap HTTP directly")
    print("=" * 60)


if __name__ == "__main__":
    _probe()
