"""Service lifecycle and asyncio I/O. Game state lives in the pure core.

S47: no SQLite call runs on this loop. The writer thread owns the live
connection; the I/O layer (`server/io.py`) freezes writes, submits them and
applies their results between steps (architecture §6.2).
"""

import asyncio
import os
import random
import shutil
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from karst41 import content as content_package
from karst41.content.loader import ContentTree, content_files, load_content
from karst41.content.validate import validate
from karst41.server.auth import Authenticator, load_secret
from karst41.server.clock import Clock, RealClock, TICK_HZ
from karst41.server.core import Core
from karst41.server.io import IO, StorageFailure
from karst41.server.logging import Logs
from karst41.server.protocol import Frame, ProtocolError, encode, read_frame
from karst41.server.sessions import Session
from karst41.server.world.registry import World
from karst41.server.storage.lease import Lease
from karst41.server.storage.writer import Writer
from karst41.server.transport import Listener
from karst41.version import PACKAGE_VERSION

STOP_SECONDS = 10  # Architecture §3.4.
DEADLINE_EXIT = 3  # Architecture §3.4: a live writer hit the stop deadline; the unit doesn't restart it.
WRITER_LOST_EXIT = 1  # Architecture §3.4: the writer thread is gone; an ordinary, restartable failure.
HELLO_SECONDS = 5  # Local transport policy, not a gameplay timer.


class Server:
    def __init__(self, install_dir: Path, tcp: bool = False, log_level: str = "INFO",
                 clock: Clock | None = None, spawn_zone: str | None = None, characters: bool = True) -> None:
        self.install_dir = install_dir.resolve()
        self.clock = clock or RealClock()
        self.log_level = log_level
        self.lease = Lease(self.install_dir / "world.db.sessions")
        self.listener = Listener(self.install_dir / "run", tcp)
        self.io: IO | None = None
        self.logs: Logs | None = None
        self.core: Core | None = None
        self.tasks: set[asyncio.Task[None]] = set()
        self.connections: set[asyncio.Task[None]] = set()
        self.stopped = asyncio.Event()
        self.stopping = False
        self.stop_requested = False
        self.spawn_zone = spawn_zone
        self.characters = characters
        self.world: World | None = None
        self.failure: BaseException | None = None
        self.exit_code = 0

    def _content(self) -> ContentTree:
        assert self.io is not None
        target = self.install_dir / "content"
        if not target.is_dir() or self.io.meta.get("content_package_version") != PACKAGE_VERSION:
            source = Path(content_package.__file__).parent
            staging = self.install_dir / "content.next"
            if staging.exists():
                raise RuntimeError("content.next exists; inspect interrupted content bootstrap")
            staging.mkdir()
            try:
                for path in content_files(source):
                    destination = staging / path.relative_to(source)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, destination)
                tree = load_content(staging, self.install_dir / "content.local")
                problems = validate(tree)
                if problems:
                    raise ValueError("invalid content: " + "; ".join(str(p) for p in problems))
            except BaseException:
                # Only remove the unpublished tree created by this attempt.
                shutil.rmtree(staging)
                raise
            previous = self.install_dir / "content.previous"
            if previous.exists():
                raise RuntimeError("content.previous exists; inspect interrupted content bootstrap")
            if target.exists():
                target.rename(previous)
            try:
                staging.rename(target)
            except BaseException:
                if previous.exists():
                    previous.rename(target)
                raise
            if previous.exists():
                shutil.rmtree(previous)
            self.io.mark("meta", "content_package_version", PACKAGE_VERSION)  # start() flushes it.
            self.io.meta["content_package_version"] = PACKAGE_VERSION
            # The atomic rename publishes the exact tree already parsed
            # and validated above. Reuse its data with the final root;
            # loading and validating the full city twice delays startup.
            tree.root = target
            return tree
        tree = load_content(target, self.install_dir / "content.local")
        problems = validate(tree)
        if problems:
            raise ValueError("invalid content: " + "; ".join(str(p) for p in problems))
        return tree

    def rebuild(self, content: ContentTree) -> None:
        """Build pure zone state from content and durable per-zone seeds."""
        assert self.io is not None
        seeds = self.io.meta.get("rng_seeds")
        if not isinstance(seeds, dict):
            raise ValueError("invalid stored zone seeds")
        changed = False
        for zone_id in sorted(content.zones):
            if zone_id not in seeds:
                seeds[zone_id] = random.SystemRandom().getrandbits(64)
                changed = True
            if type(seeds[zone_id]) is not int:
                raise ValueError("invalid stored zone seed")
        if changed:
            self.io.mark("meta", "rng_seeds", seeds)  # start() flushes it.
        self.world = World(content, seeds)

    async def start(self) -> None:
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.lease.acquire()
        try:
            for name in ("run", "secrets", "logs", "backups", "content.local"):
                path = self.install_dir / name
                path.mkdir(exist_ok=True, mode=0o700)
                if os.name != "nt" and name in {"run", "secrets"}:
                    path.chmod(0o700)
            self.logs = Logs(self.install_dir / "logs", self.clock, self.log_level)
            self.io = IO(self.clock, self.logs, Writer(self.install_dir / "world.db", self.logs))
            # The writer opens world.db, migrates and returns the startup snapshot (§3.3).
            snapshot = await self.io.bootstrap(self.characters and not self.spawn_zone,
                                               ("content_package_version", "rng_seeds", "season"))
            content = self._content()
            if self.io.dirty:
                await self.io.flush_all()
            self.rebuild(content)
            if self.io.dirty:
                await self.io.flush_all()
            auth = Authenticator(load_secret(self.install_dir / "secrets" / "door.secret"),
                                 load_secret(self.install_dir / "secrets" / "admin.secret"), self.io.banned)
            motd = content.text.get("motd")
            lines = motd.body.splitlines() if motd else []
            self.core = Core(self.clock, self.io.origin, auth,
                asdict(content.keymaps) if content.keymaps else {},
                asdict(content.palette) if content.palette else {},
                random.SystemRandom().choice(lines) if lines else "", content,
                world=self.world if self.spawn_zone or self.characters else None, spawn_zone=self.spawn_zone,
                character_state=snapshot["character_state"])
            self.io.attach(self.core)
            # Startup reconciliation commits before anyone is admitted.
            await self.io.drain()
            self.io.settle()
            self.core.audited = self.io.audit
            self.core.status_provider = self.status
            if self.stop_requested:
                self.core.request_stop()
                return
            await self.listener.start(self._connected)
            self.io.log_event("world open", content_hash=content.hash, season=self.io.season)
            await self.io.drain()  # "world open" is on disk when start() returns.
            self._task(self._ticks())
        except BaseException:
            await self.stop()
            raise

    def _character_writes(self) -> None:
        """Apply the results that have arrived, then freeze and submit (S47: never waits)."""
        if self.io is None:
            return
        self.io.settle()
        self.io.pump()

    def status(self) -> dict[str, Any]:
        assert self.core is not None and self.io is not None
        result = self.core.status()
        result["persistence_lag"] = self.io.persistence_lag()
        result["paused_ticks"] = self.io.stats["paused_ticks"]  # S47: simulation paused, writer behind.
        result.update(uptime_s=result["uptime"], tick_p50_ms=result["tick"]["p50_ms"],
                      tick_p99_ms=result["tick"]["p99_ms"], persist_lag_s=result["persistence_lag"],
                      content_hash=self.core.loop.content.hash, season=self.io.season)
        return result

    def _task(self, coroutine: Any, connections: bool = False) -> None:
        task = asyncio.create_task(coroutine)
        group = self.connections if connections else self.tasks
        group.add(task)

        def done(finished: asyncio.Task[None]) -> None:
            group.discard(finished)
            if not finished.cancelled() and finished.exception() is not None:
                if self.io:  # The fatal path writes directly: it must never wait on a writer.
                    self.io.log_event("task failed", level="ERROR", direct=True,
                                      error=type(finished.exception()).__name__)
                if not connections and self.core:
                    self.failure = finished.exception()
                    self.core.request_stop()

        task.add_done_callback(done)

    def _connected(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.stopping:
            writer.close()
            return
        self._task(self._client(reader, writer), connections=True)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        assert self.core is not None
        session: Session | None = None
        receiver: asyncio.Task[None] | None = None
        wakeup: asyncio.Task[bool] | None = None
        try:
            hello = await asyncio.wait_for(read_frame(reader, "client"), HELLO_SECONDS)
            if self.stopping:
                raise ConnectionRefusedError("service is stopping")
            session = self.core.connect(hello)
            self._character_writes()
            available = asyncio.Event()
            session.notify = available.set

            async def receive() -> None:
                assert session is not None and self.core is not None
                while not session.closed:
                    frame = await read_frame(reader, "client")
                    self.core.receive(session, frame)

            receiver = asyncio.create_task(receive())
            while True:
                while session.send_queue:
                    frame = session.send_queue.popleft()
                    writer.write(encode(frame))
                    await asyncio.wait_for(writer.drain(), 1)
                if session.closed:
                    break
                if receiver.done():
                    await receiver
                    break
                available.clear()
                wakeup = asyncio.create_task(available.wait())
                await asyncio.wait({receiver, wakeup}, return_when=asyncio.FIRST_COMPLETED)
                wakeup.cancel()
                await asyncio.gather(wakeup, return_exceptions=True)
                wakeup = None
        except asyncio.TimeoutError:
            # Before hello this is an idle handshake; afterwards the writer
            # is stalled. Neither condition says the credential was wrong.
            if session is None:
                writer.write(encode(Frame("bye", {"reason": "timeout", "text": "Connection timed out."})))
                try:
                    await asyncio.wait_for(writer.drain(), 1)
                except (OSError, asyncio.TimeoutError):
                    pass
        except (ValueError, TypeError, UnicodeError) as exc:
            reason = "banned" if str(exc) == "banned" else "protocol" if isinstance(exc, ProtocolError) else "auth"
            writer.write(encode(Frame("bye", {"reason": reason, "text": "Connection refused."})))
            try:
                await asyncio.wait_for(writer.drain(), 1)
            except (OSError, asyncio.TimeoutError):
                pass
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            if wakeup:
                wakeup.cancel()
                await asyncio.gather(wakeup, return_exceptions=True)
            if receiver:
                receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)
            try:
                if session is not None:
                    self.core.sessions.disconnect(session)
                    self._character_writes()
            finally:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 1)
                except (OSError, asyncio.TimeoutError):
                    pass

    async def _ticks(self) -> None:
        assert self.core is not None and self.io is not None
        io = self.io
        while not self.core.stop_requested:
            if io.completed:
                # Results apply between steps, as soon as they arrive (S47).
                self._character_writes()
            if self.clock.now_tick() > self.core.loop.last_tick:
                if io.step_allowed():
                    began = time.perf_counter()
                    io.stepped()
                    self.core.loop.step()
                    self._character_writes()
                    self.core.loop.record_timing(time.perf_counter() - began)
                elif io.paused_tick(self.clock.now_tick()):
                    # Paused (the writer is far behind): no simulation step, but results
                    # settle, writes go out and views publish. When steps resume, step()
                    # jumps to the current tick (loop.py: last_tick = now), as after a freeze.
                    self.core.loop.publish()
                    self._character_writes()
            io.wake.clear()
            if io.completed:
                continue
            try:
                await asyncio.wait_for(io.wake.wait(), 1 / TICK_HZ / 2)
            except asyncio.TimeoutError:
                pass

    async def run(self) -> None:
        await self.start()
        try:
            assert self.core is not None
            while not self.core.stop_requested:
                await asyncio.sleep(0.05)
            if self.failure:
                raise RuntimeError("service task failed; see logs/server.log") from self.failure
        finally:
            await self.stop()

    def request_stop(self) -> None:
        """Latch OS stop requests even before the core has been built."""
        self.stop_requested = True
        if self.core:
            self.core.request_stop()

    async def stop(self) -> None:
        """Architecture §3.4, in this order, inside one 10 s deadline:

        1. admissions: stop accepting connections;
        2. shutdown: bye to every client, the sleeper rule for everyone;
        3. pump: hand the writer every pending save, receipt, report, audit;
        4. settle: apply every in-flight receipt's result on the loop;
        5. Close: the writer flushes what is dirty and closes, on its thread;
        6. lease: release it, remove run/ files, exit 0.
        Deadline: the 10 s run out while the writer is still alive (typically
        inside an fsync): skip the rest, log what is queued or in flight, keep
        the connection and the lease untouched, exit with code 3, which the
        unit doesn't restart (a restart would only stall again).
        Writer lost: the writer thread has died. Skip the drain at once, log
        what was queued or in flight, leave the connection and the lease
        alone, and exit 1, which the unit restarts.
        """
        if self.stopping:
            await self.stopped.wait()
            return
        self.stopping = True
        io = self.io
        close_error: BaseException | None = None
        try:
            try:
                async with asyncio.timeout(STOP_SECONDS):
                    await self.listener.stop_accepting()  # 1. Admissions.
                    if self.core:
                        self.core.shutdown()  # 2. Shutdown and sleepers.
                    for task in self.tasks:
                        task.cancel()
                    await asyncio.gather(*self.tasks, return_exceptions=True)
                    if self.connections:
                        _, pending = await asyncio.wait(tuple(self.connections), timeout=2)
                        for task in pending:
                            task.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                    await self.listener.wait_closed()
                    if io is not None and io.alive:
                        try:
                            await io.drain()  # 3. Pump, and 4. settle.
                        except StorageFailure:
                            pass  # Logged where it happened; the final flush still runs.
                        if io.alive:
                            try:
                                await io.close()  # 5. Close.
                            except Exception as exc:  # The connection is closed either way.
                                close_error = exc
            except TimeoutError:
                pass
            self.listener.cleanup()
            if io is not None and not io.writer.closed:
                # Keep the connection and the lease untouched either way.
                lost = io.writer.crash is not None or not io.writer.thread.is_alive()
                io.log_event("writer lost" if lost else "stop deadline", level="ERROR", direct=True,
                             stuck=io.stuck(), queued_lines=len(io.backlog))
                # Exit 3 only for a live writer at the deadline; a lost writer is restartable (exit 1).
                self.exit_code = WRITER_LOST_EXIT if lost else DEADLINE_EXIT
            else:
                if self.logs:
                    try:
                        if close_error is not None:
                            self.logs.event("final flush failed", level="ERROR", error=type(close_error).__name__)
                        self.logs.event("world closed")
                    finally:
                        self.logs.close()
                        self.logs = None
                self.lease.release()  # 6. Lease.
        finally:
            self.stopped.set()
        if close_error is not None:
            raise close_error
