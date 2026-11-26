"""S47: the one thread that owns the live SQLite connection (architecture §6.2).

The writer creates the connection (migrations and seeds included), runs
every command on it in submission order, and closes it. The event loop
never touches it: it freezes each command as plain data, submits it here,
and applies the result when it comes back. The writer never sees a live
Actor, Player, the simulation clock or a closure. Each command brings the
tick and UTC time it was frozen at, and the writer's Storage reads those
through `CommandClock`.

The thread is a daemon. Cancelling a future can't cancel an fsync, so at
the stop deadline the process exits and leaves the thread behind; WAL
recovery applies at the next start (architecture §3.4).
"""

from __future__ import annotations

import itertools
from copy import deepcopy
import json
import queue
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from karst41.server.storage.db import Storage

BOUND = 256  # Commands queued for the writer at most (S47; the barrier keeps far fewer).
KINDS = frozenset({"bootstrap", "read", "flush", "commerce", "character", "report", "audit", "log", "close"})
RECEIPTS = frozenset({"commerce", "character"})


class LoopThreadSQLite(RuntimeError):
    """A thread other than the connection's owner used the live connection."""


class GuardedConnection(sqlite3.Connection):
    """The live connection: only the thread that opened it may use it (S47).

    sqlite3's own check_same_thread stays on as well. This guard names the
    failure, and it wraps only connections opened with this factory.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.owner = threading.get_ident()

    def _check(self) -> None:
        if threading.get_ident() != self.owner:
            raise LoopThreadSQLite("the live connection was used off the writer thread")

    def execute(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        self._check()
        return super().execute(*args, **kwargs)

    def executemany(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        self._check()
        return super().executemany(*args, **kwargs)

    def executescript(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        self._check()
        return super().executescript(*args, **kwargs)

    def cursor(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        self._check()
        return super().cursor(*args, **kwargs)

    def commit(self) -> None:
        self._check()
        super().commit()

    def rollback(self) -> None:
        self._check()
        super().rollback()

    def close(self) -> None:
        self._check()
        super().close()

    def __enter__(self) -> GuardedConnection:
        self._check()
        return super().__enter__()

    def __exit__(self, *args: Any) -> Any:
        self._check()
        return super().__exit__(*args)


class CommandClock:
    """The writer's clock: the tick and time the current command was frozen at."""

    def __init__(self) -> None:
        self.tick = 0
        self.utc = datetime(1970, 1, 1, tzinfo=timezone.utc)  # Replaced by each command.

    def now_tick(self) -> int:
        return self.tick

    def now_utc(self) -> datetime:
        return self.utc

    def now_local(self, name: str | None = None) -> datetime:
        return self.utc


_serial = itertools.count(1)


def frozen(value: Any) -> Any:
    """A deep copy the loop never touches again: the command's own data."""
    return deepcopy(value)


@dataclass(frozen=True)
class Command:
    """One frozen unit of writer work. `payload` is plain data only."""

    kind: str
    tick: int
    at: datetime
    payload: Any = None
    seq: int = field(default_factory=lambda: next(_serial))

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown writer command {self.kind!r}")


@dataclass(frozen=True)
class Result:
    """The typed outcome the writer returns for one command."""

    ok: bool
    value: Any = None
    error: BaseException | None = None
    seconds: float = 0.0  # The writer's own wall time for the command.


class Executor:
    """Runs commands on one Storage. The writer thread owns it."""

    def __init__(self, path: Path, logs: Any, factory: type[sqlite3.Connection] = GuardedConnection) -> None:
        self.path, self.logs, self.factory = path, logs, factory
        self.clock = CommandClock()
        self.storage: Storage | None = None

    def run(self, command: Command) -> Result:
        self.clock.tick, self.clock.utc = command.tick, command.at
        began = time.perf_counter()
        try:
            value = getattr(self, "_" + command.kind)(command.payload)
        except BaseException as exc:  # Typed failure; the loop decides what is fatal.
            return Result(False, error=exc, seconds=time.perf_counter() - began)
        return Result(True, value, seconds=time.perf_counter() - began)

    def _store(self) -> Storage:
        if self.storage is None:
            raise RuntimeError("the writer has no open storage")
        return self.storage

    def _bootstrap(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.storage = Storage(self.path, self.clock, factory=self.factory)
        storage = self.storage
        return {"origin": storage.origin, "bans": storage.bans(),
                "meta": {key: storage.meta(key) for key in payload["meta"]},
                "character_state": storage.character_state() if payload["characters"] else None}

    def _read(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The loop's read cache, refreshed: the whole bans table and named meta keys."""
        storage = self._store()
        return {"bans": storage.bans(), "meta": {key: storage.meta(key) for key in payload["meta"]}}

    def _batch(self, batch: dict[str, Any]) -> None:
        """Load a frozen dirty batch into the writer's scratch map."""
        storage = self._store()
        storage.dirty = {tuple(json.loads(key)): value for key, value in batch.items()}
        storage.dirty_since = {key: self.clock.tick for key in storage.dirty}

    def _flush(self, payload: dict[str, Any]) -> None:
        self._batch(payload["batch"])
        self._store()._flush(set(payload["kinds"]))

    def _commerce(self, payload: dict[str, Any]) -> None:
        self._batch({})
        self._store().commerce_write(payload)

    def _character(self, payload: dict[str, Any]) -> None:
        self._batch(payload["batch"])
        self._store().character_write(payload["effect"])

    def _report(self, payload: dict[str, Any]) -> None:
        self._store().report(payload)

    def _audit(self, payload: dict[str, Any]) -> None:
        self._store().audit(payload)

    def _log(self, payload: dict[str, Any]) -> None:
        self.logs.write(payload["sink"], payload["line"], payload.get("level", 20))

    def _close(self, payload: dict[str, Any]) -> None:
        if self.storage is None:
            return  # Bootstrap never opened it: nothing to flush or close.
        self._batch(payload["batch"])
        storage, self.storage = self._store(), None
        storage.close()  # The final flush, then the close, on this thread.


class Writer:
    """The daemon thread that owns the live connection and serves one FIFO."""

    def __init__(self, path: Path, logs: Any, factory: type[sqlite3.Connection] = GuardedConnection) -> None:
        self.executor = Executor(path, logs, factory)
        self.queue: queue.Queue[tuple[Command, Callable[[Command, Result], None]]] = queue.Queue(maxsize=BOUND)
        self.thread = threading.Thread(target=self._run, name="karst41-writer", daemon=True)
        self.loop: Any = None
        self.on_exit: Callable[[BaseException | None], None] = lambda error: None
        self.gate: Callable[[Command], None] | None = None  # Tests: hold a command before it runs.
        self.crash: BaseException | None = None
        self.closed = False

    def start(self, loop: Any) -> None:
        self.loop = loop
        self.thread.start()

    @property
    def alive(self) -> bool:
        return self.thread.is_alive() and self.crash is None

    def room(self) -> bool:
        return not self.queue.full()

    def depth(self) -> int:
        return self.queue.qsize()

    def submit(self, command: Command, done: Callable[[Command, Result], None]) -> bool:
        """Queue one command. False if the bounded queue is full (nothing queued)."""
        if not self.alive:
            return False
        try:
            self.queue.put_nowait((command, done))
        except queue.Full:
            return False
        return True

    def _deliver(self, done: Callable[[Command, Result], None], command: Command, result: Result) -> None:
        try:
            self.loop.call_soon_threadsafe(done, command, result)
        except RuntimeError:
            pass  # The loop is gone: the reply is lost, and the outcome stays unknown.

    def _run(self) -> None:
        error: BaseException | None = None
        try:
            while True:
                command, done = self.queue.get()
                if self.gate is not None:
                    self.gate(command)
                result = self.executor.run(command)
                if command.kind == "close":
                    # Before the reply: the loop reads `closed` as soon as Close's result
                    # arrives, and must never see a finished Close as a stuck writer.
                    self.closed = True
                self._deliver(done, command, result)
                if command.kind == "close":
                    return
        except BaseException as exc:
            error = self.crash = exc
        finally:
            try:
                self.loop.call_soon_threadsafe(self.on_exit, error)
            except RuntimeError:
                pass
