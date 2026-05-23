"""Health polling for the local inference server.

Used by `arc llm start` to block until the server is ready to accept
requests.  Tolerates a brief warmup window where /health may 404 (server
binding) or return `"status": "loading model"` (model still loading).
"""
from __future__ import annotations

import time


def wait_for_healthy(*, base_url: str, timeout_seconds: int,
                     poll_seconds: float = 1.0,
                     progress_cb=None) -> bool:
    """Poll `{base_url}/health` until it returns OK, or timeout.

    `progress_cb`, if provided, is called once per poll with (elapsed_seconds,
    last_status_text) so callers can render a progress bar.

    Returns True when /health flips to ok, False on timeout.
    """
    import httpx

    root = _strip_v1(base_url)
    url = f"{root}/health"

    deadline = time.monotonic() + timeout_seconds
    last_status = "?"
    start = time.monotonic()
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=2)
            if resp.status_code == 200:
                try:
                    body = resp.json()
                    status = body.get("status") if isinstance(body, dict) else None
                except Exception:
                    status = "ok"  # 200 with non-JSON body — accept as healthy
                last_status = status or "ok"
                if status in (None, "ok"):
                    return True
            else:
                last_status = f"http {resp.status_code}"
        except Exception as exc:
            last_status = f"{type(exc).__name__}"

        if progress_cb is not None:
            progress_cb(time.monotonic() - start, last_status)

        time.sleep(poll_seconds)

    return False


def _strip_v1(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base
