"""SpinnerModel — animated dots with color pulsing and live elapsed timer.

Responsibilities:
  - Tracks active/inactive state and current status message
  - Tracks the start time so the spinner shows a live elapsed counter while
    the agent is working (e.g. "Planning  ··   ·   0:05")
  - Provides get_formatted_text() for prompt_toolkit FormattedTextControl
  - tick() advances the animation frame (called by the spinner_tick task @ 0.4s)
  - update(msg) only changes the label — leaves elapsed counter intact so the
    timer keeps counting from turn start, not each stage transition
"""
from __future__ import annotations

import time
from prompt_toolkit.formatted_text import FormattedText

# Padded to fixed 3-char width so the timer that follows doesn't shift.
# The dots grow from 1 → 2 → 3 visually while occupying the same slot.
_DOTS = [".  ", ".. ", "..."]
_COLORS = ["ansicyan", "ansibrightcyan", "ansicyan", "ansigray"]


class SpinnerModel:
    def __init__(self):
        self.active: bool = False
        self.msg: str = ""
        self._frame: int = 0
        self._started_at: float = 0.0   # monotonic clock at start()

    def start(self, msg: str = "Working") -> None:
        self.active = True
        self.msg = msg
        self._frame = 0
        self._started_at = time.monotonic()

    def stop(self) -> None:
        self.active = False
        self.msg = ""
        self._started_at = 0.0

    def tick(self) -> None:
        self._frame += 1

    def update(self, msg: str) -> None:
        # Only updates the label — elapsed counter persists so it reads from
        # turn start, not each stage transition.
        self.msg = msg

    def _elapsed_str(self) -> str:
        if not self._started_at:
            return ""
        secs = int(time.monotonic() - self._started_at)
        m, s = divmod(secs, 60)
        return f"{m}:{s:02d}"

    def get_formatted_text(self) -> FormattedText:
        if not self.active:
            return FormattedText([])
        dots = _DOTS[self._frame % len(_DOTS)]
        color = _COLORS[self._frame % len(_COLORS)]
        elapsed = self._elapsed_str()
        # Dots immediately follow the status word; they are padded to a fixed
        # 3-char slot so the timer that follows stays in a stable position.
        text = f"  ⚙  {self.msg} {dots}"
        if elapsed:
            text += f"   ·   {elapsed}"
        return FormattedText([(color, text + "\n")])
