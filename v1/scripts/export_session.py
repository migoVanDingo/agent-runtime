"""Export a session for external sharing.

Bundles the session's events JSONL, blobs/, and session.summary.json into a
tarball under ``./session_<id>.tar.gz`` after applying stage-2 redaction:
hostnames, IP addresses, and absolute filesystem paths are scrubbed in
addition to the stage-1 secrets that emit-time redaction already removed.

Usage:
    python scripts/export_session.py <session_id> [--out path.tar.gz]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from session_paths import session_dir  # noqa: E402


_STAGE2_RULES: list[tuple[re.Pattern, str]] = [
    # IPv4
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ipv4>"),
    # IPv6 (compact match)
    (re.compile(r"\b(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}\b"), "<ipv6>"),
    # Absolute filesystem paths (Unix style)
    (re.compile(r"/(?:Users|home|var|opt|tmp|etc|usr|private)/\S+"), "<path>"),
    # Hostnames (best-effort): foo.bar.tld where tld is 2+ alpha
    (re.compile(r"\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b"), "<host>"),
]


def _scrub_text(text: str) -> str:
    for pattern, replacement in _STAGE2_RULES:
        text = pattern.sub(replacement, text)
    return text


def _scrub_obj(obj):
    if isinstance(obj, str):
        return _scrub_text(obj)
    if isinstance(obj, list):
        return [_scrub_obj(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _scrub_obj(v) for k, v in obj.items()}
    return obj


def _scrub_jsonl(src: Path, dst: Path) -> None:
    with open(src, "r", encoding="utf-8") as fin, open(dst, "w", encoding="utf-8") as fout:
        for line in fin:
            try:
                ev = json.loads(line)
            except Exception:
                fout.write(_scrub_text(line))
                continue
            ev = _scrub_obj(ev)
            fout.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")


def _scrub_json_file(src: Path, dst: Path) -> None:
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        dst.write_text(_scrub_text(src.read_text(encoding="utf-8")), encoding="utf-8")
        return
    dst.write_text(
        json.dumps(_scrub_obj(data), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def export(session_id: str, out_path: Path) -> Path:
    src = session_dir(session_id)
    if not src.exists():
        raise FileNotFoundError(f"no session dir at {src}")

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / session_id
        staging.mkdir(parents=True)

        # Scrub runtime.jsonl
        events = src / "events" / "runtime.jsonl"
        if events.exists():
            target_events = staging / "events" / "runtime.jsonl"
            target_events.parent.mkdir(parents=True, exist_ok=True)
            _scrub_jsonl(events, target_events)

        # Scrub blobs
        blobs = src / "events" / "blobs"
        if blobs.exists():
            for blob in blobs.glob("*.json"):
                dst = staging / "events" / "blobs" / blob.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                _scrub_json_file(blob, dst)

        # Scrub session summary
        summary = src / "session.summary.json"
        if summary.exists():
            _scrub_json_file(summary, staging / "session.summary.json")

        # Tar it up
        with tarfile.open(out_path, "w:gz") as tar:
            tar.add(staging, arcname=session_id)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("session_id")
    ap.add_argument("--out", default=None, help="output tarball path")
    args = ap.parse_args()

    out = Path(args.out) if args.out else Path.cwd() / f"session_{args.session_id}.tar.gz"
    result = export(args.session_id, out)
    print(f"Exported → {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
