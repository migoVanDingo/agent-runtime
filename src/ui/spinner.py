import sys
import time
import itertools
import threading


class Spinner:

    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, verbose: bool = False):
        self._verbose = verbose
        self._message = ""
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._turn_start: float | None = None

    def begin_turn(self) -> None:
        """Start the elapsed timer for a new user turn."""
        self._turn_start = time.monotonic()

    def elapsed_display(self) -> str:
        """Return 'M:SS' string for the current turn, or '' if timer not started."""
        if self._turn_start is None:
            return ""
        elapsed = int(time.monotonic() - self._turn_start)
        m, s = divmod(elapsed, 60)
        return f"{m}:{s:02d}"

    def _elapsed_str(self) -> str:
        if self._turn_start is None:
            return ""
        elapsed = int(time.monotonic() - self._turn_start)
        m, s = divmod(elapsed, 60)
        return f"[{m}:{s:02d}]"

    def start(self, message: str) -> None:
        if self._verbose:
            return
        self._message = message
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def update(self, message: str) -> None:
        if self._verbose:
            return
        self._message = message

    def stop(self) -> None:
        if self._verbose:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join()
        sys.stdout.write(f"\r{' ' * 80}\r")
        sys.stdout.flush()

    def _spin(self) -> None:
        last_len = 0
        for frame in itertools.cycle(self._FRAMES):
            if self._stop_event.is_set():
                break
            elapsed = self._elapsed_str()
            suffix = f"  {elapsed}" if elapsed else ""
            content = f"{frame} {self._message}{suffix}"
            sys.stdout.write(f"\r{content.ljust(last_len)}")
            sys.stdout.flush()
            last_len = len(content)
            self._stop_event.wait(0.1)
