"""S47: the loop side of persistence (architecture §6.2).

The I/O layer is the only code that talks to the writer thread. On the loop
it owns:
- the dirty map, with a generation per record, so an older result never
  clears a newer mark;
- the settlement barrier: once a receipt is submitted, nothing else that
  writes is submitted until its result has been applied on the loop;
- the read cache (bans, season, origin), filled at bootstrap;
- the log backlog: frozen lines waiting for the writer, bounded.

`pump()` freezes and submits; it never waits. `settle()` applies results in
submission order. Neither runs during a simulation step. No SQLite call
happens here: every read and write is a writer command.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections import deque
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from karst41.server.clock import TICK_HZ, Clock
from karst41.server.storage.db import IMMEDIATE_REASONS, PLAYER_FLUSH_SECONDS, WORLD_FLUSH_SECONDS
from karst41.server.storage.writer import Command, Result, frozen

LOG_BOUND = 1024  # Log lines held loop-side at most; the oldest drop first, counted.
STATS_BOUND = 8192  # Timing samples kept per figure; fixed memory for the server's lifetime.
ALL_KINDS = ("players", "items", "meta", "identities")
RECOVERABLE = (OSError, sqlite3.Error)  # A receipt that fails with these settles false.


class StorageFailure(RuntimeError):
    """A write failure the server can't carry on from (today's fatal paths)."""


class IO:
    def __init__(self, clock: Clock, logs: Any, writer: Any) -> None:
        self.clock, self.logs, self.writer = clock, logs, writer
        writer.on_exit = self._writer_exit
        self.core: Any = None
        # The dirty map: key -> frozen value, first-mark tick, generation.
        self.dirty: dict[tuple[str, str], Any] = {}
        self.dirty_since: dict[tuple[str, str], int] = {}
        self.generation: dict[tuple[str, str], int] = {}
        self.last_players = self.last_world = clock.now_tick()
        self.lattice_stamp: tuple[int, int] | None = None
        self.npc_revision: int | None = None
        # Commands submitted and not yet settled, in order, with their loop-side context.
        self.in_flight: deque[Command] = deque()
        self.context: dict[int, dict[str, Any]] = {}
        self.completed: deque[tuple[Command, Result]] = deque()
        self.awaiting: list[Command] = []  # Startup and stop commands awaited directly.
        self.barrier: Command | None = None
        self.flushing = False
        self.backlog: deque[tuple[str, str, int]] = deque()
        self.dropped = 0
        self.audits: deque[tuple[dict[str, Any], Callable[[], None]]] = deque()
        # Read cache.
        self.origin = ""
        self.bans: dict[str, str | None] = {}
        self.meta: dict[str, Any] = {}
        # Lifecycle.
        self.direct = True  # Logs go straight to disk until bootstrap returns.
        self.closing = False
        self.dead = False
        self.fatal: BaseException | None = None
        self.wake = asyncio.Event()
        # S11 figures (technical lead's ruling (e)).
        # Each timing figure is a fixed-size deque of (sample number, seconds); the
        # S11 harness reads the samples numbered after its window opened.
        self.sample = 0
        self.stats: dict[str, Any] = {key: deque(maxlen=STATS_BOUND) for key in
                                      ("commit_seconds", "latency_seconds", "apply_seconds")}
        self.stats.update(max_depth=0, ticks_in_flight_across_step=0, steps=0)

    # --- lifecycle -------------------------------------------------------

    async def bootstrap(self, characters: bool, meta: tuple[str, ...]) -> dict[str, Any]:
        """Start the writer; it opens world.db and returns the startup snapshot."""
        self.writer.start(asyncio.get_running_loop())
        result = await self._await(Command("bootstrap", self.clock.now_tick(), self.clock.now_utc(),
                                           {"characters": characters, "meta": list(meta)}))
        if not result.ok:
            assert result.error is not None
            raise result.error
        snapshot = result.value
        self.origin, self.bans, self.meta = snapshot["origin"], snapshot["bans"], dict(snapshot["meta"])
        self.direct = False
        return snapshot

    def attach(self, core: Any) -> None:
        self.core = core

    async def _await(self, command: Command, local: dict[str, Any] | None = None) -> Result:
        """Submit one command and wait for its own result (startup and stop only)."""
        future: asyncio.Future[Result] = asyncio.get_running_loop().create_future()

        def done(cmd: Command, result: Result) -> None:
            if cmd in self.awaiting:
                self.awaiting.remove(cmd)
            if not future.done():
                future.set_result(result)
        if not self.writer.submit(command, done):
            raise StorageFailure(f"the writer refused {command.kind}")
        self.awaiting.append(command)
        return await future

    async def flush_all(self) -> None:
        """Startup: commit every dirty record before anyone is admitted."""
        batch, generations = self._batch(ALL_KINDS)
        result = await self._await(Command("flush", self.clock.now_tick(), self.clock.now_utc(),
                                           {"kinds": list(ALL_KINDS), "batch": batch}))
        if not result.ok:
            assert result.error is not None
            raise result.error
        self._clear(generations)
        self.last_players = self.last_world = self.clock.now_tick()

    def _writer_exit(self, error: BaseException | None) -> None:
        """The writer thread ended (on the loop)."""
        self.dead = True
        if self.closing and error is None:
            return
        self.direct = True
        if self.logs is not None:
            self._direct_backlog()  # Held lines go out now, in order (technical lead, change 2).
        self.fatal = self.fatal or StorageFailure("the writer stopped" + (f": {error!r}" if error else ""))
        for command in self.in_flight:
            if command.kind in ("commerce", "character", "report", "audit", "flush"):
                self.log_event("commit outcome unknown", level="ERROR", command=command.kind, seq=command.seq,
                               tick=command.tick)
        self.wake.set()

    @property
    def alive(self) -> bool:
        return not self.dead and self.writer.alive

    def idle(self) -> bool:
        """Nothing in flight, nothing waiting to settle, nothing waiting to submit."""
        core = self.core
        outboxes = bool(core and ((core.characters and core.characters.outbox)
                                  or (core.commerce and core.commerce.outbox)
                                  or (core.chat and core.chat.outbox)))
        return not (self.in_flight or self.completed or self.backlog or self.audits or outboxes)

    async def drain(self) -> None:
        """Pump and settle until everything accepted has settled (the stop path)."""
        while True:
            self.settle()
            self.pump()
            if self.idle() or not self.alive:
                return
            self.wake.clear()
            if self.completed:
                continue
            await self.wake.wait()

    async def close(self) -> None:
        """Close: the writer flushes what is still dirty and closes the connection."""
        self.closing = True
        batch, generations = self._batch(ALL_KINDS)
        result = await self._await(Command("close", self.clock.now_tick(), self.clock.now_utc(), {"batch": batch}))
        self.direct = True
        if not result.ok:
            assert result.error is not None
            raise result.error
        self._clear(generations)

    def stuck(self) -> list[dict[str, Any]]:
        """The commands still queued or in flight (the deadline log)."""
        return [{"command": c.kind, "seq": c.seq, "tick": c.tick} for c in (*self.in_flight, *self.awaiting)]

    # --- the dirty map ---------------------------------------------------

    def mark(self, kind: str, record_id: str, value: Any) -> None:
        """Freeze one record's state now; a later mark of the same record replaces it."""
        if kind not in ALL_KINDS:
            raise ValueError("unknown persistent entity kind")
        key = (kind, record_id)
        self.dirty[key] = json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))
        self.dirty_since.setdefault(key, self.clock.now_tick())
        self.generation[key] = self.generation.get(key, 0) + 1

    def persistence_lag(self) -> float:
        """Age of the oldest uncommitted proposal, zero when nothing is dirty."""
        now = self.clock.now_tick()
        return (now - min(self.dirty_since.values(), default=now)) / TICK_HZ

    def _batch(self, kinds: tuple[str, ...] | set[str]) -> tuple[dict[str, Any], dict[tuple[str, str], int]]:
        keys = [key for key in self.dirty if key[0] in kinds]
        batch = {json.dumps(list(key)): self.dirty[key] for key in keys}
        return batch, {key: self.generation[key] for key in keys}

    def _clear(self, generations: dict[tuple[str, str], int]) -> None:
        """A durable result clears only the marks it carried, never a newer one."""
        for key, generation in generations.items():
            if self.generation.get(key) == generation:
                self.dirty.pop(key, None)
                self.dirty_since.pop(key, None)

    def _drop(self, key: tuple[str, str]) -> None:
        self.dirty.pop(key, None)
        self.dirty_since.pop(key, None)
        self.generation[key] = self.generation.get(key, 0) + 1

    # --- reads -----------------------------------------------------------

    def banned(self, user_id: str) -> bool:
        expires = self.bans.get(user_id, False)
        return expires is not False and (expires is None or expires > self.clock.now_utc().isoformat())

    @property
    def season(self) -> Any:
        return self.meta.get("season")

    # --- logs ------------------------------------------------------------

    def log_event(self, message: str, level: str = "INFO", direct: bool = False, **fields: Any) -> None:
        if self.logs is None or not self.logs.enabled(level):
            return
        self._log("server", self.logs.event_line(message, level, **fields), getattr(logging, level), direct)

    def log_audit(self, row: dict[str, Any], direct: bool = False) -> None:
        if self.logs is not None:
            self._log("audit", self.logs.audit_line(row), logging.INFO, direct)

    def _log(self, sink: str, line: str, level: int, direct: bool) -> None:
        if direct or self.direct or not self.alive or self.fatal is not None:
            self._direct_backlog()
            self.logs.write(sink, line, level, timeout=1.0)
            return
        if len(self.backlog) >= LOG_BOUND:
            self.backlog.popleft()
            self.dropped += 1
        self.backlog.append((sink, line, level))

    def _direct_backlog(self) -> None:
        """Lines still held loop-side go out first, in order, when writing directly."""
        while self.backlog:
            sink, line, level = self.backlog.popleft()
            self.logs.write(sink, line, level, timeout=1.0)
        if self.dropped:
            dropped, self.dropped = self.dropped, 0
            self.logs.write("server", self.logs.event_line("log lines dropped", "WARNING", count=dropped),
                            logging.WARNING, timeout=1.0)

    # --- admin audits ----------------------------------------------------

    def audit(self, row: dict[str, Any], then: Callable[[], None]) -> None:
        """Admin audit: `then` (the reply, and a stop) runs on its durable result."""
        self.audits.append((deepcopy(row), then))

    # --- pump: freeze and submit, never wait ------------------------------

    def _submit(self, kind: str, payload: Any, local: dict[str, Any] | None = None) -> Command | None:
        if not self.alive or not self.writer.room():
            return None
        command = Command(kind, self.clock.now_tick(), self.clock.now_utc(), payload)
        if not self.writer.submit(command, self._done):
            return None
        self.in_flight.append(command)
        self.context[command.seq] = {"submitted": time.perf_counter(), **(local or {})}
        self.stats["max_depth"] = max(self.stats["max_depth"], len(self.in_flight))
        return command

    def _done(self, command: Command, result: Result) -> None:
        """The writer's reply, on the loop. Applied by settle(), in order."""
        self.completed.append((command, result))
        self.wake.set()

    def mark_world(self) -> None:
        """Mark the world records the loop re-freezes every pass (as before S47)."""
        core = self.core
        if core.contracts:
            self.mark("meta", "contracts_state", core.contracts.record())
        if core.salvage:
            self.mark("meta", "salvage_state", {**core.salvage.state, "uptime": core.salvage.uptime})
        if core.lattice:
            sources = core.lattice.sources
            tick = self.clock.now_tick()
            stamp = sources.revision, tick // (30 * TICK_HZ)
            if stamp != self.lattice_stamp:
                self.mark("meta", "lattice_state", sources.record(tick))
                self.lattice_stamp = stamp
        if core.npcs and self.npc_revision != core.npcs.spawners.revision:
            self.mark("meta", "npc_state", core.npcs.spawners.record())
            self.npc_revision = core.npcs.spawners.revision

    def _recorders(self) -> dict[str, Callable[[], Any]]:
        core = self.core
        found: dict[str, Callable[[], Any]] = {}
        if core.contracts:
            found["contracts_state"] = core.contracts.record
        if core.salvage:
            found["salvage_state"] = lambda: {**core.salvage.state, "uptime": core.salvage.uptime}
        if core.lattice:
            found["lattice_state"] = lambda: core.lattice.sources.record(self.clock.now_tick())
        if core.npcs:
            found["npc_state"] = core.npcs.spawners.record
        return found

    def pump(self) -> None:
        """Freeze what the simulation produced and submit it, in today's order."""
        core = self.core
        if core is None:
            return
        if core.events:
            while core.events.diagnostics:
                self.log_event("event source unavailable", level="WARNING", **core.events.diagnostics.pop(0))
        game = core.characters
        if game is not None:
            self.mark_world()
        if self.barrier is not None or not self.alive or self.fatal is not None:
            return
        # Lines, reports and audits frozen earlier go first, in order.
        while self.backlog and self.writer.room():
            sink, line, level = self.backlog[0]
            if self._submit("log", {"sink": sink, "line": line, "level": level}) is None:
                return
            self.backlog.popleft()
        if self.dropped and not self.backlog:
            dropped, self.dropped = self.dropped, 0
            self.log_event("log lines dropped", level="WARNING", count=dropped)
        chat = core.chat
        while chat and chat.outbox:
            effect = chat.outbox[0]
            if self._submit("report", frozen(effect["row"]), {"effect": effect}) is None:
                return
            chat.outbox.pop(0)
        while self.audits:
            row, then = self.audits[0]
            if self._submit("audit", frozen(row), {"row": row, "then": then}) is None:
                return
            self.audits.popleft()
        if game is None:
            return
        # The character outbox before commerce, as before S47.
        while game.outbox and self.barrier is None:
            effect = game.outbox[0]
            if not self._character(effect):
                return
            game.outbox.pop(0)
        commerce = core.commerce
        while commerce and commerce.outbox and self.barrier is None:
            effect = commerce.outbox[0]
            if not commerce.can_commit(effect):
                commerce.outbox.pop(0)
                commerce.committed(effect, False)  # May queue more; the loop continues.
                continue
            prepared = commerce.prepare(effect)
            command = self._submit("commerce", frozen(prepared), {"effect": effect, "prepared": prepared})
            if command is None:
                return
            commerce.outbox.pop(0)
            self.barrier = command
        if self.barrier is None:
            self._flush_due()

    def _character(self, effect: dict[str, Any]) -> bool:
        """One character effect. False: the writer has no room; try next pump."""
        action = effect["action"]
        if action != "delete" and not (action in ("create", "identity") or effect.get("immediate")):
            # A plain save only marks, as character_write did.
            self._mark_effect(effect)
            self.core.characters.committed(effect)
            return True
        payload: dict[str, Any] = {"effect": frozen(effect), "batch": {}}
        generations: dict[tuple[str, str], int] = {}
        if action != "delete":
            payload["batch"], generations = self._batch(ALL_KINDS)
        command = self._submit("character", payload, {"effect": effect, "generations": generations})
        if command is None:
            return False
        self.barrier = command
        return True

    def _mark_effect(self, effect: dict[str, Any]) -> None:
        self.mark("identities", effect["user"], effect["identity"])
        if effect.get("player"):
            self.mark("players", effect["player"]["id"], effect["player"])
        for item in effect.get("items", []):
            self.mark("items", item["id"], item)
        if "serial" in effect:
            self.mark("meta", "player_serial", effect["serial"])

    def _flush_due(self) -> None:
        if self.flushing:
            return
        tick = self.clock.now_tick()
        kinds: set[str] = set()
        if tick - self.last_players >= PLAYER_FLUSH_SECONDS * TICK_HZ:
            kinds.update({"players", "identities"})
        if tick - self.last_world >= WORLD_FLUSH_SECONDS * TICK_HZ:
            kinds.update({"meta", "items"})
        if not kinds:
            return
        batch, generations = self._batch(kinds)
        if self._submit("flush", {"kinds": sorted(kinds), "batch": batch}, {"generations": generations}) is None:
            return
        self.flushing = True
        if "players" in kinds:
            self.last_players = tick
        if "meta" in kinds:
            self.last_world = tick

    # --- settle: apply results on the loop, in order -----------------------

    def settle(self) -> None:
        """Apply every result that has arrived. Raises StorageFailure on a fatal one."""
        if self.fatal is not None and not self.completed:
            raise StorageFailure(str(self.fatal)) from self.fatal
        while self.completed:
            command, result = self.completed.popleft()
            head = self.in_flight.popleft()
            assert head is command, "the writer answered out of order"
            local = self.context.pop(command.seq)
            self._sample("commit_seconds", result.seconds)
            began = time.perf_counter()
            try:
                getattr(self, "_settle_" + command.kind)(command, result, local)
            except StorageFailure as exc:
                self.fatal = exc
                self.direct = True
                raise
            finally:
                if command.kind in ("commerce", "character"):
                    self._sample("latency_seconds", time.perf_counter() - local["submitted"])
                self._sample("apply_seconds", time.perf_counter() - began)
        if self.fatal is not None:
            raise StorageFailure(str(self.fatal)) from self.fatal

    def _fatal(self, what: str, result: Result) -> None:
        self.log_event(what, level="ERROR", direct=True, error=type(result.error).__name__)
        raise StorageFailure(what) from result.error

    def _settle_flush(self, command: Command, result: Result, local: dict[str, Any]) -> None:
        self.flushing = False
        if not result.ok:
            # The dirty state stays for the next flush; the failure stops the server, as before.
            self._fatal("flush failed", result)
        self._clear(local["generations"])

    def _settle_log(self, command: Command, result: Result, local: dict[str, Any]) -> None:
        if not result.ok:
            self.log_event("log write failed", level="ERROR", direct=True, error=type(result.error).__name__)

    def _settle_report(self, command: Command, result: Result, local: dict[str, Any]) -> None:
        chat, effect = self.core.chat, local["effect"]
        if result.ok:
            self.log_audit(effect["row"])  # After its commit; a log failure is only a log failure.
            chat.committed(effect, True)
        elif isinstance(result.error, RECOVERABLE):
            chat.committed(effect, False)
            self.log_event("report commit failed", level="ERROR")
        else:
            self._fatal("report commit failed", result)

    def _settle_audit(self, command: Command, result: Result, local: dict[str, Any]) -> None:
        if not result.ok:
            self._fatal("audit commit failed", result)  # Ruling (d): fatal, as before.
        self.log_audit(local["row"])
        local["then"]()

    def _settle_character(self, command: Command, result: Result, local: dict[str, Any]) -> None:
        self.barrier = None
        effect, game = local["effect"], self.core.characters
        action = effect["action"]
        if result.ok:
            if action == "delete":
                self._purge_deleted(effect)
            else:
                self._clear(local["generations"])
                self.last_players = self.last_world = command.tick
            game.committed(effect)
            return
        if action in ("create", "delete"):
            # A rejected create never reached the loop's map: nothing leaks into a later flush.
            game.committed(effect, error=True)
            self.log_event("character commit failed", level="ERROR", action=action)
            return
        self._mark_effect(effect)  # Ordinary state keeps its frozen snapshot, as before.
        self._fatal("character commit failed", result)

    def _purge_deleted(self, effect: dict[str, Any]) -> None:
        record_id, user = effect["player"]["id"], effect["user"]
        for key in tuple(self.dirty):
            if key in (("players", record_id), ("identities", user)) or (
                    key[0] == "items" and isinstance(self.dirty[key], dict) and self.dirty[key].get("owner") == record_id):
                self._drop(key)

    def _settle_commerce(self, command: Command, result: Result, local: dict[str, Any]) -> None:
        self.barrier = None
        commerce, effect = self.core.commerce, local["effect"]
        if not result.ok:
            if not isinstance(result.error, RECOVERABLE):
                self._fatal("item transfer commit failed", result)
            commerce.committed(effect, False)
            self.log_event("item transfer commit failed", level="ERROR")
            return
        commerce.committed(effect, True)
        self._refreeze(local["prepared"])

    def _refreeze(self, prepared: dict[str, Any]) -> None:
        """Every dirty record the receipt covers is re-frozen from settled live state.

        A mark made before the receipt was prepared but not yet flushed is
        older than the receipt; one made after holds newer fields but no
        reward. Either would overwrite the durable reward if flushed as it
        is (technical lead, change 1).
        """
        core = self.core
        game, commerce = core.characters, core.commerce
        players = {row["id"] for row in ([prepared["player"]] if prepared.get("player") else [])}
        players |= {row["id"] for row in prepared.get("players", [])}
        users = set(prepared.get("identities", {}))
        if "identity" in prepared:
            users.add(prepared["user"])
        users |= {game.players[pid].bbs_user_id for pid in players if pid in game.players}
        items = {row["id"] for row in prepared["items"]} | set(prepared["delete_items"])
        recorders = self._recorders()
        for key in tuple(self.dirty):
            kind, record_id = key
            if kind == "players" and record_id in players:
                player = game.players.get(record_id)
                self.mark(kind, record_id, player.record()) if player else self._drop(key)
            elif kind == "identities" and record_id in users:
                identity = game.identities.get(record_id)
                self.mark(kind, record_id, identity) if identity is not None else self._drop(key)
            elif kind == "items" and record_id in items:
                item = commerce.items.get(record_id)
                self.mark(kind, record_id, item.record()) if item is not None else self._drop(key)
            elif kind == "meta" and record_id in prepared and record_id in recorders:
                self.mark(kind, record_id, recorders[record_id]())

    # --- S11 bookkeeping --------------------------------------------------

    def _sample(self, figure: str, seconds: float) -> None:
        self.sample += 1
        self.stats[figure].append((self.sample, seconds))

    def samples(self, figure: str, after: int = 0) -> list[float]:
        """The figure's samples numbered after `after` (still held: at most STATS_BOUND)."""
        return [seconds for number, seconds in self.stats[figure] if number > after]

    def stepped(self) -> None:
        """Called once per simulation step: count a receipt in flight across it."""
        self.stats["steps"] += 1
        if self.barrier is not None:
            self.stats["ticks_in_flight_across_step"] += 1


__all__ = ["IO", "StorageFailure", "LOG_BOUND", "IMMEDIATE_REASONS"]
