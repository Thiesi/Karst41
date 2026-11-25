"""S47: database saves never stall a tick (slice S47, architecture §6.2).

Deterministic: every commit is held and released by hand through
ManualWriter, with no sleeps and no timing thresholds. Each test names the
slice line it covers.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from karst41.content.loader import load_content
from karst41.server.io import IO, LOG_BOUND, StorageFailure
from karst41.server.protocol import Frame
from karst41.server.storage.db import Storage
from tests.character_helpers import act, create, place
from tests.harness import ADMIN_SECRET, HeadlessServer, hello
from tests.server.manual_writer import ManualWriter, run_writes


class FakeLogs:
    """Captures frozen lines; which path wrote them shows in `writer.ran`."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, dict[str, Any]]] = []

    def enabled(self, level: str) -> bool:
        return True

    def event_line(self, message: str, level: str = "INFO", **fields: Any) -> str:
        return json.dumps({"message": message, "level": level, **fields})

    def audit_line(self, row: dict[str, Any]) -> str:
        return json.dumps(row)

    def write(self, sink: str, line: str, level: int = 20, timeout: float = -1) -> None:
        self.lines.append((sink, json.loads(line)))

    def messages(self) -> list[str]:
        return [line.get("message", "") for _, line in self.lines]


class Rig:
    """A headless world whose writes go through IO and a ManualWriter."""

    def __init__(self, tmp_path: Path, content: Any, users: int = 1) -> None:
        self.game = HeadlessServer(content, character_state={})
        self.store = Storage(tmp_path / "world.db", self.game.clock)
        self.clients = []
        for n in range(users):
            client = self.game.connect({"user_id": n + 1, "handle": f"Caller{n + 1}", "node_name": "test"})
            for effect in create(self.game, client, name=f"Runner{n + 1}"):
                self.store.character_write(effect)
            self.clients.append(client)
        self.game.core.characters.outbox.clear()
        self.logs = FakeLogs()
        self.writer = ManualWriter(logs=self.logs, storage=self.store)
        self.io = IO(self.game.clock, self.logs, self.writer)
        self.io.direct = False
        self.io.attach(self.game.core)
        self.game.core.audited = self.io.audit
        self.path = tmp_path / "world.db"

    def cycle(self) -> None:
        """What Server._character_writes does after each step and on each result."""
        self.io.settle()
        self.io.pump()

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            self.game.step()
            self.cycle()

    def player(self, n: int = 0) -> Any:
        return self.game.core.characters.players[self.clients[n].session.player_id]

    def saved(self, n: int = 0) -> dict[str, Any] | None:
        """The committed row, read through a fresh connection (what a restart sees)."""
        fresh = Storage(self.path, self.game.clock)
        try:
            pid = self.clients[n].session.player_id
            return next((row for row in fresh.character_state()["players"] if row["id"] == pid), None)
        finally:
            fresh.db.close()

    def reward(self, player: Any, chits: int, calls: list[bool]) -> None:
        commerce = self.game.core.commerce
        assert commerce.settle(player, {"chits_hand": player.chits_hand + chits}, dict(commerce.items),
                               commerce.caches, commerce.serial, calls.append)


@pytest.fixture(scope="module")
def content() -> Any:
    tree = load_content(Path(__file__).parents[2] / "src/karst41/content")
    tree.walls = None  # The wall voice is S48d's; these tests count log lines.
    return tree


@pytest.fixture
def rig(tmp_path: Path, content: Any) -> Rig:
    return Rig(tmp_path, content)


# --- Tests: "Hold a commit while ticks advance" ---------------------------

def test_a_held_receipt_shows_nothing_early_and_the_loop_keeps_ticking(rig: Rig) -> None:
    player, calls = rig.player(), []
    before, tick = player.chits_hand, rig.game.clock.now_tick()
    rig.reward(player, 50, calls)
    rig.cycle()
    assert rig.writer.kinds() == ["commerce"]
    rig.step(30)  # Three seconds of ticks while the commit is held.
    assert rig.game.clock.now_tick() == tick + 30
    assert player.chits_hand == before and calls == [] and rig.game.core.commerce.pending
    assert rig.writer.kinds() == ["commerce"], "nothing else is submitted behind an unsettled receipt"
    rig.writer.complete()
    rig.cycle()
    assert player.chits_hand == before + 50 and calls == [True] and not rig.game.core.commerce.pending
    assert rig.saved()["chits_hand"] == before + 50


def test_a_held_creation_attaches_no_body_until_its_commit(tmp_path: Path, content: Any) -> None:
    rig = Rig(tmp_path, content)
    client = rig.game.connect({"user_id": 9, "handle": "Late", "node_name": "test"})
    create(rig.game, client, name="Late", commit=False)
    rig.cycle()
    assert rig.writer.kinds() == ["character"]
    manager = rig.game.core.characters
    held = manager.pending[client.session.id][0]
    rig.step(10)
    assert held.id not in manager.players or manager.unread(held.id)
    assert rig.store.db.execute("SELECT count(*) FROM players WHERE id=?", (held.id,)).fetchone()[0] == 0
    run_writes(rig.io)
    assert held.id in manager.players and not manager.unread(held.id)


def test_a_held_report_is_not_acknowledged_until_its_commit(tmp_path: Path, content: Any) -> None:
    rig = Rig(tmp_path, content, users=2)
    a, b = rig.clients
    place(rig.game, a, "core-plaza", (10, 5))
    place(rig.game, b, "core-plaza", (15, 5))
    rig.game.frames_for(a)
    act(rig.game, a, Frame("intent", {"name": "report", "args": {"name": "Runner2", "text": "unsafe"}}))
    rig.cycle()
    assert rig.writer.kinds() == ["report"]
    rig.step(5)
    assert not any(f.type == "toast" and f.data["text"] == "Reported." for f in rig.game.frames_for(a))
    run_writes(rig.io)
    assert any(f.type == "toast" and f.data["text"] == "Reported." for f in rig.game.frames_for(a))
    assert rig.store.db.execute("SELECT count(*) FROM reports").fetchone()[0] == 1
    assert ("audit", ) == tuple({sink for sink, line in rig.logs.lines if line.get("action") == "report"})


def test_a_held_admin_audit_holds_the_reply(tmp_path: Path, content: Any) -> None:
    rig = Rig(tmp_path, content)
    operator = rig.game.connect(frame=hello(secret=ADMIN_SECRET))
    rig.game.frames_for(operator)
    rig.game.core.receive(operator.session, Frame("admin.who"))
    rig.step()
    assert rig.writer.kinds() == ["audit"]
    rig.step(5)
    assert not [f for f in rig.game.frames_for(operator) if f.type == "view"]
    run_writes(rig.io)
    assert [f for f in rig.game.frames_for(operator) if f.type == "view"]
    assert rig.store.db.execute("SELECT count(*) FROM audit").fetchone()[0] >= 1


# --- Tests: FIFO and the settlement barrier --------------------------------

def test_fifo_saves_then_receipt_then_the_saves_its_callback_queues(rig: Rig) -> None:
    player, manager = rig.player(), rig.game.core.characters
    manager.queue("save", player.bbs_user_id, player)  # An ordinary save: marked, flushed later.
    commerce = rig.game.core.commerce
    after: list[str] = []

    def callback(ok: bool) -> None:
        after.append("callback")
        manager.queue("save", player.bbs_user_id, player, immediate=True)
    assert commerce.settle(player, {"chits_hand": player.chits_hand + 7}, dict(commerce.items),
                           commerce.caches, commerce.serial, callback)
    rig.cycle()
    assert ("players", player.id) in rig.io.dirty and rig.writer.kinds() == ["commerce"]
    rig.writer.complete()
    rig.cycle()  # Settles, then submits the callback's immediate save.
    assert after == ["callback"] and rig.writer.kinds() == ["character"]
    run_writes(rig.io)
    assert [c.kind for c in rig.writer.ran] == ["commerce", "character"]
    assert rig.saved()["chits_hand"] == player.chits_hand


def test_an_unflushed_mark_older_than_the_receipt_never_overwrites_its_reward(rig: Rig) -> None:
    """Technical lead, change 1: re-freeze every dirty entry for the receipt's records."""
    player, manager = rig.player(), rig.game.core.characters
    before = player.chits_hand
    manager.queue("save", player.bbs_user_id, player)
    rig.cycle()  # Marked, not flushed: the frozen row has the old chits.
    assert rig.io.dirty[("players", player.id)]["chits_hand"] == before
    calls: list[bool] = []
    rig.reward(player, 50, calls)
    rig.cycle()
    rig.step(10)  # Held across ticks.
    rig.writer.complete()
    rig.cycle()
    assert rig.io.dirty[("players", player.id)]["chits_hand"] == before + 50, "re-frozen from settled state"
    rig.game.clock.advance(60)  # The 5 s player flush comes due.
    rig.cycle()
    assert rig.writer.kinds() == ["flush"]
    run_writes(rig.io)
    assert rig.saved()["chits_hand"] == before + 50


def test_a_mark_made_while_the_receipt_is_held_keeps_its_newer_fields_and_the_reward(rig: Rig) -> None:
    player, manager = rig.player(), rig.game.core.characters
    before = player.chits_hand
    calls: list[bool] = []
    rig.reward(player, 50, calls)
    rig.cycle()
    player.health = max(1, player.health - 3)  # Changes while the commit is held...
    manager.queue("save", player.bbs_user_id, player)  # ...and its save waits behind the barrier.
    rig.step(3)
    assert ("players", player.id) not in rig.io.dirty
    run_writes(rig.io)
    rig.game.clock.advance(60)
    rig.cycle()
    run_writes(rig.io)
    saved = rig.saved()
    assert saved["chits_hand"] == before + 50 and saved["health"] == player.health


# --- Tests: deletion and disconnect against an in-flight receipt -----------

def test_a_delete_queued_while_a_receipt_is_in_flight_commits_after_it_settles(rig: Rig) -> None:
    player, manager = rig.player(), rig.game.core.characters
    calls: list[bool] = []
    rig.reward(player, 50, calls)
    rig.cycle()
    manager.deleting.add(player.id)
    manager.queue("delete", player.bbs_user_id, player, token=rig.clients[0].session.id)
    rig.cycle()
    assert rig.writer.kinds() == ["commerce"], "the delete waits behind the barrier"
    run_writes(rig.io)
    assert [c.kind for c in rig.writer.ran][:2] == ["commerce", "character"]
    assert rig.writer.ran[1].payload["effect"]["action"] == "delete"
    assert calls == [True] and player.id not in manager.players
    assert rig.saved() is None
    assert ("players", player.id) not in rig.io.dirty


def test_a_receipt_still_settles_after_its_caller_disconnects(rig: Rig) -> None:
    player, calls = rig.player(), []
    before = player.chits_hand
    rig.reward(player, 50, calls)
    rig.cycle()
    rig.game.core.sessions.disconnect(rig.clients[0].session)
    rig.step(2)
    run_writes(rig.io)
    assert calls == [True] and rig.saved()["chits_hand"] == before + 50


# --- Tests: failures -------------------------------------------------------

def test_a_rejected_create_never_reaches_a_later_flush(tmp_path: Path, content: Any) -> None:
    rig = Rig(tmp_path, content)
    rig.store.db.execute("CREATE TRIGGER reject BEFORE INSERT ON players BEGIN SELECT RAISE(ABORT,'probe'); END")
    client = rig.game.connect({"user_id": 9, "handle": "Late", "node_name": "test"})
    create(rig.game, client, name="Late", commit=False)
    rig.cycle()
    held = rig.game.core.characters.pending[client.session.id][0]
    run_writes(rig.io)
    assert ("players", held.id) not in rig.io.dirty
    assert "character commit failed" in rig.logs.messages()
    rig.store.db.execute("DROP TRIGGER reject")
    rig.game.clock.advance(400)
    rig.cycle()
    run_writes(rig.io)
    assert rig.store.db.execute("SELECT count(*) FROM players WHERE id=?", (held.id,)).fetchone()[0] == 0


def test_a_failed_receipt_rolls_back_settles_false_and_keeps_its_error(rig: Rig) -> None:
    player, calls = rig.player(), []
    before = player.chits_hand
    rig.store.db.execute("CREATE TRIGGER reject BEFORE UPDATE ON players BEGIN SELECT RAISE(ABORT,'probe'); END")
    rig.reward(player, 50, calls)
    rig.cycle()
    [result] = rig.writer.complete()
    assert isinstance(result.error, sqlite3.IntegrityError) and "probe" in str(result.error)
    rig.cycle()
    assert calls == [False] and player.chits_hand == before and not rig.game.core.commerce.pending
    run_writes(rig.io)
    assert "item transfer commit failed" in rig.logs.messages()
    rig.store.db.execute("DROP TRIGGER reject")
    assert rig.saved()["chits_hand"] == before


def test_a_failed_flush_keeps_its_dirty_state_and_stops_the_server(rig: Rig) -> None:
    player, manager = rig.player(), rig.game.core.characters
    manager.queue("save", player.bbs_user_id, player)
    rig.cycle()
    rig.store.db.execute("CREATE TRIGGER reject BEFORE UPDATE ON players BEGIN SELECT RAISE(ABORT,'probe'); END")
    rig.game.clock.advance(60)
    rig.cycle()
    assert rig.writer.kinds() == ["flush"]
    rig.writer.complete()
    with pytest.raises(StorageFailure):
        rig.cycle()
    assert ("players", player.id) in rig.io.dirty
    assert rig.writer.kinds() == [], "no retry of its own"
    with pytest.raises(StorageFailure):
        rig.io.settle()  # Still fatal: the server's stop path takes it from here.


def test_a_lost_reply_is_an_unknown_outcome_never_a_rollback(rig: Rig) -> None:
    player, calls = rig.player(), []
    before = player.chits_hand
    rig.reward(player, 50, calls)
    rig.cycle()
    rig.writer.complete(lose=True)  # Committed; the reply never arrives.
    rig.writer.die()
    with pytest.raises(StorageFailure):
        rig.cycle()
    assert calls == [] and rig.game.core.commerce.pending, "neither acknowledged nor rolled back"
    assert "commit outcome unknown" in rig.logs.messages()
    assert not any(f.type == "toast" and "could not be saved" in f.data.get("text", "")
                   for f in rig.game.frames_for(rig.clients[0]))
    assert [c.kind for c in rig.writer.ran] == ["commerce"], "never retried"
    assert rig.saved()["chits_hand"] == before + 50  # WAL has it; a restart loads it once.


# --- Tests: restarts against real storage ----------------------------------

def restart(rig: Rig, content: Any) -> HeadlessServer:
    fresh = Storage(rig.path, rig.game.clock)
    try:
        return HeadlessServer(content, character_state=fresh.character_state(), clock=rig.game.clock)
    finally:
        fresh.db.close()


def test_restart_after_a_crash_before_commit_has_no_reward(rig: Rig, content: Any) -> None:
    player, calls = rig.player(), []
    before = player.chits_hand
    rig.reward(player, 50, calls)
    rig.cycle()
    rig.writer.die()  # The command never ran.
    restored = restart(rig, content)
    assert restored.core.characters.players[player.id].chits_hand == before


def test_restart_after_commit_but_before_the_callback_has_the_reward_once(rig: Rig, content: Any) -> None:
    player, calls = rig.player(), []
    before = player.chits_hand
    rig.reward(player, 50, calls)
    rig.cycle()
    rig.writer.complete(lose=True)
    rig.writer.die()
    restored = restart(rig, content)
    assert restored.core.characters.players[player.id].chits_hand == before + 50
    assert calls == [] and [c.kind for c in rig.writer.ran] == ["commerce"]


def test_restart_with_ordinary_state_queued_keeps_every_acknowledged_reward(rig: Rig, content: Any) -> None:
    player, manager = rig.player(), rig.game.core.characters
    calls: list[bool] = []
    rig.reward(player, 50, calls)
    run_writes(rig.io)
    rewarded = player.chits_hand
    player.health = max(1, player.health - 5)
    manager.queue("save", player.bbs_user_id, player)  # Ordinary state, marked, never flushed.
    rig.cycle()
    assert ("players", player.id) in rig.io.dirty
    rig.writer.die()
    restored = restart(rig, content).core.characters.players[player.id]
    assert calls == [True] and restored.chits_hand == rewarded  # Acknowledged: durable.
    # The unflushed health change is the at-most-5-s loss §6.2 allows, and nothing stale replaced the reward.


# --- Tests: logs -----------------------------------------------------------

def test_log_lines_go_through_the_writer_after_the_commit_they_report(rig: Rig) -> None:
    rig.io.log_event("hello")
    assert rig.logs.lines == [] and len(rig.io.backlog) == 1
    rig.cycle()
    assert rig.writer.kinds() == ["log"]
    run_writes(rig.io)
    assert rig.logs.messages() == ["hello"]


def test_logs_are_written_directly_before_bootstrap_and_after_the_writer_dies(rig: Rig) -> None:
    rig.io.direct = True
    rig.io.log_event("before bootstrap")
    assert rig.logs.messages() == ["before bootstrap"] and not rig.io.backlog
    rig.io.direct = False
    rig.io.log_event("held")
    rig.writer.die()
    assert rig.logs.messages()[:2] == ["before bootstrap", "held"], "the backlog goes out first, in order"
    rig.io.log_event("after")
    assert rig.logs.messages()[-1] == "after"


def test_the_log_backlog_is_bounded_drops_the_oldest_and_counts_them(rig: Rig) -> None:
    calls: list[bool] = []
    rig.reward(rig.player(), 1, calls)
    rig.cycle()  # The barrier holds every line loop-side.
    for n in range(LOG_BOUND + 76):
        rig.io.log_event("line", n=n)
    assert len(rig.io.backlog) == LOG_BOUND and rig.io.dropped == 76
    run_writes(rig.io)
    lines = [line for _, line in rig.logs.lines]
    assert [line["n"] for line in lines if line.get("message") == "line"] == list(range(76, LOG_BOUND + 76))
    assert {"message": "log lines dropped", "level": "WARNING", "count": 76} in lines


def test_a_full_writer_queue_holds_work_loop_side_and_shows_todays_busy_text(tmp_path: Path, content: Any) -> None:
    rig = Rig(tmp_path, content)
    rig.writer.bound = 0  # Nothing fits.
    calls: list[bool] = []
    rig.reward(rig.player(), 5, calls)
    rig.cycle()
    assert rig.game.core.commerce.outbox and not rig.io.in_flight
    rig.writer.bound = 256
    run_writes(rig.io)
    assert calls == [True]


def test_the_timing_figures_hold_fixed_memory_for_the_servers_lifetime(rig: Rig) -> None:
    """Technical lead's review, item 1: no per-command growth in the live server."""
    from karst41.server.io import STATS_BOUND
    for n in range(STATS_BOUND + 500):
        rig.io.log_event("line", n=n)
        rig.cycle()
        rig.writer.complete()
    rig.cycle()
    for figure in ("commit_seconds", "latency_seconds", "apply_seconds"):
        assert len(rig.io.stats[figure]) <= STATS_BOUND
    assert len(rig.io.stats["commit_seconds"]) == STATS_BOUND
    assert len(rig.io.samples("commit_seconds", rig.io.sample - 10)) <= 10


def test_a_failed_immediate_save_never_overwrites_a_newer_mark_at_the_stop(rig: Rig) -> None:
    """Technical lead's review, item 2: the stop's Close flushes the newer state."""
    import asyncio
    player, manager = rig.player(), rig.game.core.characters
    old_health = player.health
    manager.queue("save", player.bbs_user_id, player, immediate=True)
    rig.cycle()
    assert rig.writer.kinds() == ["character"]
    rig.store.db.execute("CREATE TRIGGER reject BEFORE UPDATE ON players BEGIN SELECT RAISE(ABORT,'probe'); END")
    newer = {**player.record(), "health": old_health - 7}
    rig.io.mark("players", player.id, newer)  # Marked after submission: newer than the effect's snapshot.
    rig.writer.complete()
    with pytest.raises(StorageFailure):
        rig.cycle()
    assert rig.io.dirty[("players", player.id)]["health"] == old_health - 7
    rig.store.db.execute("DROP TRIGGER reject")

    async def stop() -> None:
        closing = asyncio.ensure_future(rig.io.close())
        await asyncio.sleep(0)
        rig.writer.complete()
        await closing
    asyncio.run(stop())
    assert rig.writer.closed
    assert rig.saved()["health"] == old_health - 7  # Read back through a fresh connection.
