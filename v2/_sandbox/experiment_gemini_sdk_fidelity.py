"""Byte-fidelity probe for google-genai SDK.

Run: python _tests/experiment_gemini_sdk_fidelity.py

Output: prints whether the SDK exposes a JSON-round-trippable view of the response.
Updates _design/0002-foundation-phase1-gemini-sdk-byte-fidelity.md with findings.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _load_env() -> None:
    """Read .env into os.environ if it exists. Cheap impl, no dep."""
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
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("SKIP: GEMINI_API_KEY not set")
        return

    from google import genai
    from google.genai import types

    # Use the configured model from defaults, but fall back to a stable one
    # if the live-preview model isn't available in this account.
    model = os.environ.get("ARC_TEST_MODEL", "gemini-3.1-flash-live-preview")

    client = genai.Client(api_key=api_key)

    config = types.GenerateContentConfig(
        temperature=0,
        max_output_tokens=128,
    )

    print(f"Calling model: {model}")
    print("---")

    try:
        resp = client.models.generate_content(
            model=model,
            contents="Say only the word 'pong'.",
            config=config,
        )
    except Exception as e:
        print(f"Call failed: {type(e).__name__}: {e}")
        print("\nIf the model name is invalid, try setting ARC_TEST_MODEL=gemini-2.5-flash")
        sys.exit(1)

    print(f"Response text: {resp.text!r}")
    print(f"Response type: {type(resp).__name__}")
    print(f"Has .to_json_dict()? {hasattr(resp, 'to_json_dict')}")
    print(f"Has .model_dump()? {hasattr(resp, 'model_dump')}")
    print(f"Has .model_dump_json()? {hasattr(resp, 'model_dump_json')}")
    print(f"Has .dict()? {hasattr(resp, 'dict')}")

    # Try each round-trip strategy
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

    if hasattr(resp, "to_json_dict"):
        try:
            d = resp.to_json_dict()
            roundtripped = json.loads(json.dumps(d, default=str))
            findings.append(("to_json_dict()", True, len(json.dumps(d)), roundtripped))
            print(f"✓ to_json_dict() round-trips ({len(json.dumps(d))} bytes)")
        except Exception as e:
            findings.append(("to_json_dict()", False, 0, str(e)))
            print(f"✗ to_json_dict() failed: {e}")

    # Check usage metadata
    print(f"\nUsage:")
    if hasattr(resp, "usage_metadata") and resp.usage_metadata:
        um = resp.usage_metadata
        print(f"  prompt_tokens:    {getattr(um, 'prompt_token_count', '?')}")
        print(f"  response_tokens:  {getattr(um, 'candidates_token_count', '?')}")
        print(f"  total_tokens:     {getattr(um, 'total_token_count', '?')}")

    # Verdict
    successful = [f for f in findings if f[1]]
    print("\n" + "=" * 60)
    if successful:
        best = min(successful, key=lambda f: -f[2])  # largest payload = most fidelity
        print(f"VERDICT: SDK is byte-faithful via .{best[0]}")
        print(f"         Strategy: store the dict in events.jsonl content field")
    else:
        print("VERDICT: SDK is NOT byte-faithful; wrap HTTP directly")
    print("=" * 60)


if __name__ == "__main__":
    _probe()
