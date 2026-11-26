"""Instance-owned JSONL sinks; no root logger or terminal output.

S47: in the live server the loop freezes each line (its `at` included) and
the writer thread writes it, in order after the commit it reports. The loop
writes directly only when no live writer can: before bootstrap, after the
writer has died or closed, and on the fatal and deadline paths. One lock
keeps the two from interleaving a line.
"""

import json
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from karst41.server.clock import Clock

LOCK_SECONDS = 1.0  # A direct write never waits longer on a stuck writer.


class Logs:
    def __init__(self, root: Path, clock: Clock, level: str = "INFO") -> None:
        self.clock = clock
        self.level = getattr(logging, level)
        self.server = RotatingFileHandler(root / "server.log", maxBytes=2 * 1024 * 1024,
                                         backupCount=3, encoding="utf-8")
        self.audit_file = (root / "audit.log").open("a", encoding="utf-8")
        self.lock = threading.Lock()
        self.stuck = False  # A direct write already timed out on the lock: later ones don't wait.

    def enabled(self, level: str) -> bool:
        return getattr(logging, level) >= self.level

    def event_line(self, message: str, level: str = "INFO", **fields: Any) -> str:
        """Freeze one server.log line now, on the loop."""
        return json.dumps({"at": self.clock.now_utc().isoformat(), "level": level,
                           "message": message, **fields}, ensure_ascii=False)

    def audit_line(self, row: dict[str, Any]) -> str:
        """Freeze one audit.log line now, on the loop."""
        return json.dumps({"at": self.clock.now_utc().isoformat(), **row}, ensure_ascii=False)

    def write(self, sink: str, line: str, level: int = logging.INFO, timeout: float = -1) -> None:
        """Write one frozen line. `timeout` bounds the wait for the lock (direct writes).

        The first direct write that times out marks the lock stuck; later
        direct writes then don't wait at all until the lock is free again, so
        a whole direct backlog costs at most one LOCK_SECONDS (review
        7167769920: the stop must exit 3 well inside TimeoutStopSec).
        """
        wait = 0 if timeout >= 0 and self.stuck else timeout
        if not self.lock.acquire(timeout=wait):
            if timeout >= 0:
                self.stuck = True
            print(line, file=sys.stderr)  # The writer is stuck mid-write: never lose the line.
            return
        self.stuck = False
        try:
            if sink == "audit":
                self.audit_file.write(line + "\n")
                self.audit_file.flush()
            else:
                self.server.emit(logging.LogRecord("karst41", level, "", 0, line, (), None))
        finally:
            self.lock.release()

    def event(self, message: str, level: str = "INFO", **fields: Any) -> None:
        """A direct, synchronous line (offline tools, and the loop when no writer can)."""
        if self.enabled(level):
            self.write("server", self.event_line(message, level, **fields), getattr(logging, level), LOCK_SECONDS)

    def audit(self, row: dict[str, Any]) -> None:
        self.write("audit", self.audit_line(row), timeout=LOCK_SECONDS)

    def close(self) -> None:
        self.server.close()
        self.audit_file.close()
