"""Exceptions raised across the service boundary."""


class TurnCancelledError(Exception):
    """Raised by TurnHandle.wait() when a turn is cancelled before completion."""
    def __init__(self, at_stage: str = "") -> None:
        self.at_stage = at_stage
        super().__init__(f"Turn cancelled at: {at_stage}" if at_stage else "Turn cancelled")


class TurnFailedError(Exception):
    """Raised by TurnHandle.wait() when the agent raises an unhandled exception."""
    def __init__(self, message: str = "") -> None:
        super().__init__(message)
