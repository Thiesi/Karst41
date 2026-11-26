"""S47 with the real writer thread: the guard, the drain, and the deadline.

The ordering and failure cases run deterministically in test_s47_writer.py
with manual gates. These tests check what only a real thread shows: no
SQLite on the loop after admission, and the stop sequence of architecture
§3.4, including its deadline branch and exit code 3.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from karst41.server import app
from karst41.server.app import DEADLINE_EXIT, WRITER_LOST_EXIT, Server
from karst41.server.storage.lease import Lease
from karst41.server.storage.writer import LoopThreadSQLite
from tests import storage_reader
from tests.server.test_lifecycle import login


def test_the_guard_fires_for_the_live_connection_from_the_loop_after_admission(scratch_dir: Path) -> None:
    async def scenario() -> None:
        server = Server(scratch_dir, tcp=True)
        await server.start()
        try:
            reader, writer = await login(scratch_dir)
            live = server.io.writer.executor.storage.db
            with pytest.raises(LoopThreadSQLite):
                live.execute("SELECT 1")
            with pytest.raises(LoopThreadSQLite):
                with live:
                    pass
            # The tests' own read-only connection neither trips the guard nor writes.
            with storage_reader.loop_guard():
                assert storage_reader.count(scratch_dir / "world.db", "SELECT count(*) FROM meta") > 0
                with storage_reader.reader(scratch_dir / "world.db") as store:
                    with pytest.raises(sqlite3.OperationalError):
                        store.db.execute("INSERT INTO meta(key,value) VALUES('x','1')")
                # Any other connection the loop opens is caught: the reader is no loophole.
                with pytest.raises(storage_reader.LoopSQLite):
                    sqlite3.connect(scratch_dir / "world.db")
            writer.close()
        finally:
            await server.stop()
        assert server.exit_code == 0
    asyncio.run(scenario())


def test_no_sqlite_on_the_loop_thread_for_a_whole_run(scratch_dir: Path) -> None:
    """Slice S47 tests: fails if any SQLite connection opens on the loop after admission."""
    async def scenario() -> None:
        server = Server(scratch_dir, tcp=True)
        await server.start()
        try:
            with storage_reader.loop_guard():
                reader, writer = await login(scratch_dir)
                for _ in range(20):
                    await asyncio.sleep(0.05)  # Ticks, flush cadence, the client's own writes.
                assert server.io.alive and server.io.fatal is None
                writer.close()
                await writer.wait_closed()
                await asyncio.sleep(0.2)
        finally:
            await server.stop()
        assert server.exit_code == 0
    asyncio.run(scenario())


def test_stop_drains_closes_on_the_writer_thread_then_releases_the_lease(scratch_dir: Path) -> None:
    async def scenario() -> None:
        server = Server(scratch_dir, tcp=True)
        await server.start()
        server.io.mark("meta", "s47_probe", {"written": True})
        thread = server.io.writer.thread
        await server.stop()
        assert server.exit_code == 0 and server.io.writer.closed and not thread.is_alive()
        assert storage_reader.meta(scratch_dir / "world.db", "s47_probe") == {"written": True}
        lease = Lease(scratch_dir / "world.db.sessions")
        lease.acquire()  # Released.
        lease.release()
    asyncio.run(scenario())


def test_a_held_close_hits_the_deadline_exits_3_and_keeps_the_lease(scratch_dir: Path, monkeypatch) -> None:
    monkeypatch.setattr(app, "STOP_SECONDS", 0.5)
    release = threading.Event()

    async def scenario() -> None:
        server = Server(scratch_dir, tcp=True)
        await server.start()
        server.io.writer.gate = lambda command: release.wait(10) if command.kind == "close" else None
        try:
            await server.stop()
            assert server.exit_code == DEADLINE_EXIT  # A live writer stuck in a command: exit 3.
            with pytest.raises(Exception, match="lease"):
                Lease(scratch_dir / "world.db.sessions").acquire()  # Still held: never released early.
            log = (scratch_dir / "logs" / "server.log").read_text(encoding="utf-8")
            assert '"stop deadline"' in log and '"close"' in log
            assert server.io.writer.thread.is_alive(), "the connection was not closed from the loop"
        finally:
            release.set()
            server.io.writer.thread.join(5)
            server.lease.release()
    asyncio.run(scenario())


def test_a_writer_killed_mid_run_exits_1_at_once_and_names_what_was_in_flight(scratch_dir: Path) -> None:
    """Technical lead, change 4 as corrected: no 10 s wait on a corpse, and a restartable exit."""
    async def scenario() -> None:
        server = Server(scratch_dir, tcp=True)
        await server.start()

        def crash(command: object) -> None:
            raise RuntimeError("disk gone")
        server.io.writer.gate = crash
        server.io.log_event("trigger")
        server._character_writes()
        await asyncio.wait_for(asyncio.to_thread(server.io.writer.thread.join, 5), 6)
        await asyncio.sleep(0)  # The writer's exit notice reaches the loop.
        began = time.monotonic()
        try:
            await server.stop()
            assert time.monotonic() - began < 3
            assert server.exit_code == WRITER_LOST_EXIT == 1
            log = (scratch_dir / "logs" / "server.log").read_text(encoding="utf-8")
            lost = next(json.loads(row) for row in log.splitlines() if '"writer lost"' in row)
            assert lost["stuck"] and lost["stuck"][0]["command"] == "log", lost  # What it died holding.
            assert '"stop deadline"' not in log
        finally:
            server.lease.release()
    asyncio.run(scenario())


def test_the_s11_guard_matches_this_world_db_by_target_however_it_is_named(tmp_path: Path, monkeypatch) -> None:
    """Technical lead's choice (C): the harness check names the database, never the calling code."""
    world, other = tmp_path / "world.db", tmp_path / "netbbs.db"
    for path in (world, other):
        sqlite3.connect(path).close()
    monkeypatch.chdir(tmp_path)
    with storage_reader.loop_guard(database=world):
        for spelled in ("world.db", str(world), world, str(world).encode(),
                        f"file:{world.as_posix()}?mode=ro", "file:world.db?mode=rw",
                        "file:///" + world.as_posix().lstrip("/")):
            uri = isinstance(spelled, str) and spelled.startswith("file:")
            with pytest.raises(storage_reader.LoopSQLite):
                sqlite3.connect(spelled, uri=uri)
        sqlite3.connect(other).close()  # Another database (NetBBS's own) passes.
        sqlite3.connect(":memory:").close()
        with storage_reader.reader(world) as store:  # The read-only test reader passes.
            store.db.execute("SELECT 1")
    assert storage_reader.target(("file:world.db?mode=ro",), {}) == world.resolve()


def test_a_hand_ban_applies_to_a_live_server_with_the_loop_guard_armed(scratch_dir: Path, monkeypatch) -> None:
    from karst41.server import io as io_module
    from karst41.server.auth import load_secret
    from karst41.server.protocol import encode, read_frame
    from karst41.server.transport import connect
    from tests.harness import hello
    monkeypatch.setattr(io_module, "REFRESH_SECONDS", 1)
    db = scratch_dir / "world.db"

    def edit(sql: str) -> None:  # The operator's own process: never the loop thread.
        operator = sqlite3.connect(db, timeout=5)
        with operator:
            operator.execute(sql)
        operator.close()

    async def first_frame() -> str:
        reader, writer = await connect(scratch_dir)
        writer.write(encode(hello(secret=load_secret(scratch_dir / "secrets" / "door.secret"))))
        await writer.drain()
        frame = await asyncio.wait_for(read_frame(reader, "server"), 2)
        writer.close()
        return frame.type if frame.type != "bye" else "bye:" + frame.data["reason"]

    async def scenario() -> None:
        server = Server(scratch_dir, tcp=True)
        await server.start()
        try:
            with storage_reader.loop_guard(database=db):
                assert await first_frame() == "welcome"
                await asyncio.to_thread(edit, "INSERT INTO bans(bbs_user_id, expires_at, reason) VALUES('1', NULL, 'probe')")
                await asyncio.sleep(1.5)
                assert await first_frame() == "bye:banned"
                await asyncio.to_thread(edit, "DELETE FROM bans WHERE bbs_user_id='1'")
                await asyncio.sleep(1.5)
                assert await first_frame() == "welcome"
        finally:
            await server.stop()
        assert server.exit_code == 0
    asyncio.run(scenario())


def test_a_writer_that_dies_during_startup_fails_start_instead_of_hanging(scratch_dir: Path) -> None:
    """Automated review 7167698411: an awaited startup command never waits on a dead writer."""
    from karst41.server.io import StorageFailure
    from karst41.server.storage import writer as writer_module

    class DyingWriter(writer_module.Writer):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.gate = self.crash_on_bootstrap

        def crash_on_bootstrap(self, command) -> None:
            if command.kind == "bootstrap":
                raise RuntimeError("thread lost")

    async def scenario() -> None:
        server = Server(scratch_dir, tcp=True)
        original = app.Writer
        app.Writer = DyingWriter
        try:
            with pytest.raises(StorageFailure):
                await asyncio.wait_for(server.start(), 5)  # Fails, never hangs.
        finally:
            app.Writer = original
        assert server.exit_code == WRITER_LOST_EXIT  # A lost writer is an ordinary, restartable failure.
        server.lease.release()
    asyncio.run(scenario())


def test_a_direct_backlog_behind_a_stuck_log_lock_waits_once_not_per_line(tmp_path: Path, capsys) -> None:
    """Automated review 7167769920: the direct flush must stay well inside TimeoutStopSec."""
    from karst41.server.clock import ManualClock
    from karst41.server.logging import LOCK_SECONDS, Logs
    logs = Logs(tmp_path, ManualClock())
    logs.lock.acquire()  # The writer is stuck mid-write, holding the lock.
    try:
        began = time.monotonic()
        for n in range(30):
            logs.event("held line", n=n)
        elapsed = time.monotonic() - began
        assert elapsed < LOCK_SECONDS + 1, f"{elapsed:.1f} s for 30 direct lines"
        assert capsys.readouterr().err.count("held line") == 30  # Every line reaches stderr.
    finally:
        logs.lock.release()
    logs.event("after")  # The lock is free again: the next write waits normally and lands.
    assert not logs.stuck
    logs.close()
    assert "after" in (tmp_path / "server.log").read_text(encoding="utf-8")


def test_close_is_marked_closed_before_its_reply_reaches_the_loop(scratch_dir: Path) -> None:
    """A clean stop must never read a finished Close as a stuck writer (exit 3, lease kept)."""
    from karst41.server.storage import writer as writer_module
    seen: list[bool] = []

    class Watching(writer_module.Writer):
        def _deliver(self, done, command, result) -> None:
            if command.kind == "close":
                seen.append(self.closed)
            super()._deliver(done, command, result)

    async def scenario() -> None:
        server = Server(scratch_dir, tcp=True)
        original = app.Writer
        app.Writer = Watching
        try:
            await server.start()
            await server.stop()
        finally:
            app.Writer = original
        assert seen == [True] and server.exit_code == 0
    asyncio.run(scenario())
