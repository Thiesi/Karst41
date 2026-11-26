"""S47: tests and acceptance scripts read world.db through their own connection.

The live server's connection belongs to its writer thread, and the loop
never touches SQLite (architecture §6.2). A test that wants to see what was
committed opens a separate, read-only connection here instead. WAL lets it
read alongside the writer. It can't write (`mode=ro`), and it never touches
the live connection, so the writer's guard neither fires for it nor is
bypassed by it.

`loop_guard()` arms the check the slice asks for: any SQLite connection the
armed thread opens, other than through this reader, raises. The live
connection's own guard is GuardedConnection, by connection identity.
"""

from __future__ import annotations

import contextlib
import os
import re
import sqlite3
import threading
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from karst41.server.clock import ManualClock
from karst41.server.storage.db import Storage

_local = threading.local()


class LoopSQLite(AssertionError):
    """The armed (loop) thread opened an SQLite connection of its own."""


def connect(path: Path) -> sqlite3.Connection:
    """A read-only connection to a world.db the live server may be writing."""
    _local.reader = True
    try:
        db = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True, timeout=5.0)
    finally:
        _local.reader = False
    db.row_factory = sqlite3.Row
    return db


@contextlib.contextmanager
def reader(path: Path) -> Iterator[Storage]:
    """Storage's read methods (meta, character_state, bans) over a read-only connection."""
    store = Storage.__new__(Storage)
    store.db, store.clock = connect(path), ManualClock()
    store.dirty, store.dirty_since = {}, {}
    try:
        yield store
    finally:
        store.db.close()


def meta(path: Path, key: str) -> Any:
    with reader(path) as store:
        return store.meta(key)


def character_state(path: Path) -> dict[str, Any]:
    with reader(path) as store:
        return store.character_state()


def count(path: Path, sql: str, *args: Any) -> int:
    with reader(path) as store:
        return store.db.execute(sql, args).fetchone()[0]


def target(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Path | None:
    """The database file an sqlite3.connect call opens, resolved; None for memory."""
    database = args[0] if args else kwargs.get("database")
    if database is None:
        return None
    text = os.fsdecode(database) if isinstance(database, bytes) else os.fspath(database)
    if text.startswith("file:"):
        path = urllib.parse.urlsplit(text).path or text[5:].split("?", 1)[0]
        text = urllib.parse.unquote(path)
        if re.match(r"^/[A-Za-z]:", text):
            text = text[1:]  # file:///C:/... on Windows.
    if text in ("", ":memory:"):
        return None
    return Path(text).resolve()


@contextlib.contextmanager
def loop_guard(thread: int | None = None, database: Path | None = None) -> Iterator[None]:
    """Fail if `thread` (default: this one) opens an SQLite connection other than a reader's.

    `database` limits the check to connections whose target is that file
    (S11: this install's world.db; the in-process NetBBS node keeps its own
    database on the same loop). Targets compare as resolved paths, and
    `file:` URIs with any `?mode=` count as their file.
    """
    armed = thread if thread is not None else threading.get_ident()
    original = sqlite3.connect
    watched = Path(database).resolve() if database is not None else None

    def guarded(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        if threading.get_ident() == armed and not getattr(_local, "reader", False):
            opened = target(args, kwargs)
            if watched is None or opened == watched:
                raise LoopSQLite(f"an SQLite connection to {opened or 'memory'} was opened on the loop thread")
        return original(*args, **kwargs)
    sqlite3.connect = guarded  # type: ignore[assignment]
    try:
        yield
    finally:
        sqlite3.connect = original  # type: ignore[assignment]

async def committed_state(service: Any) -> dict[str, Any]:
    """What a live server has committed: settle everything accepted, then read it.

    Acceptance scripts used to read through the server's own connection.
    That connection is the writer thread's now (S47), so they read here.
    """
    await service.io.drain()
    return character_state(service.install_dir / "world.db")
