"""S47 with the real writer thread: the guard, the drain, and the deadline.

The ordering and failure cases run deterministically in test_s47_writer.py
with manual gates. These tests check what only a real thread shows: no
SQLite on the loop after admission, and the stop sequence of architecture
§3.4, including its deadline branch and exit code 3.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from karst41.server import app
from karst41.server.app import DEADLINE_EXIT, Server
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
            assert server.exit_code == DEADLINE_EXIT
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


def test_a_dead_writer_takes_the_deadline_branch_at_once(scratch_dir: Path) -> None:
    """Technical lead, change 4: no 10 s wait on a corpse."""
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
            assert server.exit_code == DEADLINE_EXIT
            log = (scratch_dir / "logs" / "server.log").read_text(encoding="utf-8")
            assert '"stop deadline"' in log
        finally:
            server.lease.release()
    asyncio.run(scenario())
