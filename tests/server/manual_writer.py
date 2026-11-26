"""S47 tests: the writer's command API with manual completion gates.

`ManualWriter` runs the same `Executor` (the same SQL) as the live writer
thread, but only when a test says so, on the test's thread. A test can
hold a command while ticks advance, complete it, lose its reply, or kill
the writer, with no sleeps and no timing thresholds.
"""

from __future__ import annotations

import sqlite3
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from karst41.server.io import IO
from karst41.server.storage.db import Storage
from karst41.server.storage.writer import BOUND, Command, Executor, Result


class ManualWriter:
    def __init__(self, path: Path | None = None, logs: Any = None, storage: Storage | None = None,
                 bound: int = BOUND) -> None:
        self.executor = Executor(path or Path("unused.db"), logs, sqlite3.Connection)
        self.executor.storage = storage
        self.queue: deque[tuple[Command, Callable[[Command, Result], None]]] = deque()
        self.bound = bound
        self.on_exit: Callable[[BaseException | None], None] = lambda error: None
        self.closed = False
        self.dead = False
        self.ran: list[Command] = []

    def start(self, loop: Any) -> None:
        pass

    @property
    def alive(self) -> bool:
        return not self.dead and not self.closed

    def room(self) -> bool:
        return len(self.queue) < self.bound

    def depth(self) -> int:
        return len(self.queue)

    def submit(self, command: Command, done: Callable[[Command, Result], None]) -> bool:
        if not self.alive or not self.room():
            return False
        self.queue.append((command, done))
        return True

    def kinds(self) -> list[str]:
        return [command.kind for command, _ in self.queue]

    def complete(self, n: int | None = None, lose: bool = False) -> list[Result]:
        """Run the next `n` queued commands (all if None). `lose` drops their replies."""
        results = []
        while self.queue and (n is None or len(results) < n):
            command, done = self.queue.popleft()
            result = self.executor.run(command)
            self.ran.append(command)
            results.append(result)
            if command.kind == "close":
                self.closed = True
            if not lose:
                done(command, result)
        return results

    def die(self, error: BaseException | None = None) -> None:
        """The writer thread ends without answering what it holds."""
        self.dead = True
        self.on_exit(error or RuntimeError("writer died"))


def run_writes(io: IO, rounds: int = 100) -> None:
    """Pump, complete and settle until nothing is left (a synchronous stand-in for the loop)."""
    writer = io.writer
    for _ in range(rounds):
        io.settle()
        io.pump()
        if not writer.queue and not io.completed:
            if io.idle() or not io.in_flight:
                return
        writer.complete()
    raise AssertionError("writes did not settle")


def attach(server: Any, storage: Storage, core: Any = None) -> ManualWriter:
    """Give a Server built without start() a ManualWriter over an open Storage."""
    writer = ManualWriter(storage=storage)
    server.io = IO(server.clock, None, writer)
    server.io.direct = False
    if core is not None:
        server.core = core
    server.io.attach(server.core)
    return writer
