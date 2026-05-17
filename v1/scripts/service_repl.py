#!/usr/bin/env python3
"""Service-layer REPL — drives the agent through InProcessAgentService.

Prints every AgentEvent as it arrives so you can validate the service
architecture before the Textual UI exists.

Usage:
    python scripts/service_repl.py
    python scripts/service_repl.py --session-id my-session
"""
from __future__ import annotations

import asyncio
import sys
import os

# Ensure src/ is on the path when running as a script.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import argparse
from dataclasses import asdict


def _event_summary(event) -> str:
    d = asdict(event)
    core = f"[{event.type}]"
    extras = {k: v for k, v in d.items() if k not in ("type", "session_id", "timestamp", "turn_id") and v}
    if extras:
        short = ", ".join(f"{k}={str(v)[:60]}" for k, v in list(extras.items())[:3])
        core += f"  {short}"
    return core


async def _event_printer(service) -> None:
    """Subscribe to all events and print summaries."""
    async for event in service.events():
        print(f"  EVENT  {_event_summary(event)}", flush=True)


async def run(session_id: str) -> None:
    from agent import Agent
    from service.inprocess import InProcessAgentService
    from runtime.events import init_runtime_events
    from logger import configure_logging

    configure_logging(session_id, verbose=False)
    init_runtime_events(session_id)

    agent = Agent(verbose=False)
    svc = InProcessAgentService(agent, session_id=session_id)

    # Start background event printer.
    printer_task = asyncio.create_task(_event_printer(svc))

    print(f"\nService REPL  |  session={session_id}")
    print("Type a message and press Enter. Type 'exit' to quit.\n")

    loop = asyncio.get_event_loop()

    try:
        while True:
            try:
                line = await loop.run_in_executor(None, input, "You: ")
            except EOFError:
                break

            line = line.strip()
            if not line:
                continue
            if line.lower() in ("exit", "quit"):
                break

            handle = await svc.send(line)
            response = await handle.wait()
            print(f"\nAgent: {response}\n")

    except KeyboardInterrupt:
        pass
    finally:
        printer_task.cancel()
        await svc.close()
        print("\nSession ended.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Service-layer REPL")
    parser.add_argument("--session-id", default=f"repl-{os.getpid()}", help="Session ID")
    args = parser.parse_args()
    asyncio.run(run(args.session_id))


if __name__ == "__main__":
    main()
