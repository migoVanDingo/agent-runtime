import sys
import time
import itertools
import threading


class Spinner:

    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    # Erase current line + return to start — more reliable than bare \r alone.
    _ERASE = "\033[2K\r"

    def __init__(self, verbose: bool = False):
        self._verbose = verbose
        self._tty = sys.stdout.isatty()
        self._message = ""
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._turn_start: float | None = None

    def _active(self) -> bool:
        return not self._verbose and self._tty

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
        if not self._active():
            return
        # Stop any live thread before starting a new one — prevents duplicate
        # threads from fighting over stdout.
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            self._thread.join(timeout=0.5)
            self._thread = None
        self._message = message
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def update(self, message: str) -> None:
        if not self._active():
            return
        self._message = message

    def stop(self) -> None:
        if not self._active():
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join()
            self._thread = None
        sys.stdout.write(self._ERASE)
        sys.stdout.flush()

    def _spin(self) -> None:
        for frame in itertools.cycle(self._FRAMES):
            if self._stop_event.is_set():
                break
            elapsed = self._elapsed_str()
            suffix = f"  {elapsed}" if elapsed else ""
            content = f"{frame} {self._message}{suffix}"
            sys.stdout.write(f"{self._ERASE}{content}")
            sys.stdout.flush()
            self._stop_event.wait(0.1)
