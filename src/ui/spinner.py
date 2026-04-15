import sys
import itertools
import threading


class Spinner:

    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, verbose: bool = False):
        self._verbose = verbose
        self._message = ""
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

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
        sys.stdout.write(f"\r{' ' * (len(self._message) + 4)}\r")
        sys.stdout.flush()

    def _spin(self) -> None:
        for frame in itertools.cycle(self._FRAMES):
            if self._stop_event.is_set():
                break
            line = f"\r{frame} {self._message}"
            sys.stdout.write(line)
            sys.stdout.flush()
            self._stop_event.wait(0.1)
