"""#277 (C1): coalescing superseded plain saves within one outbox drain.

A Sable enforcer's shot queues two or three plain saves of the same player
in one tick. Within one drain, a plain save whose records a later plain
save in the same drain marks again is not marked itself. These tests run
the same scripted fight twice, with coalescing on and off (the reference),
and require the same dirty map after every pump, the same commands
submitted to the writer, the same rows on disk, the same world, and the
same recovered database when the writer dies at each step around a
coalescing drain.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from karst41.content.loader import load_content
from karst41.server.io import IO, StorageFailure
from tests.character_helpers import place
from tests.server.test_s47_writer import Rig


@pytest.fixture(scope="module")
def content() -> Any:
    tree = load_content(Path(__file__).parents[2] / "src/karst41/content")
    tree.walls = None  # As the S47 rig's tests.
    return tree


@pytest.fixture
def reference(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Run a body with coalescing off (the uncoalesced S47 path)."""
    def off() -> None:
        monkeypatch.setattr(IO, "COALESCE_PLAIN_SAVES", False)

    def on() -> None:
        monkeypatch.setattr(IO, "COALESCE_PLAIN_SAVES", True)
    return off, on


class Fight:
    """A caller on Sodium Row, provoked Sable enforcers, writes through IO."""

    def __init__(self, tmp: Path, content: Any) -> None:
        tmp.mkdir(parents=True, exist_ok=True)
        self.coalesced_drains, self.me = 0, None
        self.rig = Rig(tmp, content)
        self.game = self.rig.game
        caller = self.rig.clients[0]
        place(self.game, caller, "sodium-row", (16, 5))
        self.cycle()
        for zone in self.game.core.world.zones.values():
            zone.activate("test-277", self.game.clock.now_tick())
        for _ in range(40):
            self.tick()
        npcs, zone = self.game.core.npcs, self.game.core.world.zones["sodium-row"]
        self.me = self.game.core.characters.sim.avatars[caller.session.user_id].actor
        guards = [a for a in zone.actors.values() if a.behaviour.get("template") == "npc-sable-enforcer"]
        assert len(guards) >= 2
        npcs.provoked(self.me, min(guards, key=lambda a: ((a.x - self.me.x) ** 2 + (a.y - self.me.y) ** 2, a.id)))
        self.coalesced_drains = 0  # Count from the fight on.

    def cycle(self, complete: bool = True) -> None:
        io = self.rig.io
        if io.barrier is None and io.alive and io.fatal is None and self.game.core.characters.outbox:
            self.coalesced_drains += bool(io._superseded(self.game.core.characters.outbox))
        self.rig.cycle()
        if complete:
            self.rig.writer.complete()

    def tick(self, complete: bool = True) -> None:
        if self.me is not None and self.me.health < 40:
            self.me.health = 130
        self.game.step()
        self.cycle(complete)

    def snapshot(self) -> dict[str, Any]:
        io = self.rig.io
        return {"dirty": dict(io.dirty), "since": dict(io.dirty_since), "barrier": io.barrier is not None,
                "in_flight": [(c.kind, c.tick, c.payload) for c in io.in_flight]}

    def commands(self) -> list[tuple[str, int, Any]]:
        return [(c.kind, c.tick, c.payload) for c in self.rig.writer.ran]

    def world(self) -> Any:
        actors = sorted((a.id, a.zone, a.position, a.health, a.behaviour.get("state"), a.target)
                        for a in self.game.core.world.actors.values())
        return actors, [z.rng.getstate() for _, z in sorted(self.game.core.world.zones.items())]

    def close(self) -> None:
        self.rig.store.db.close()


def database(path: Path) -> dict[str, list[tuple[Any, ...]]]:
    """Every row of every table, read through a fresh connection (what a restart sees).

    Each database draws its own random origin at creation; it is replaced by
    a placeholder so two runs compare on what the game wrote.
    """
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        origin = json.loads(db.execute("SELECT value FROM meta WHERE key='origin'").fetchone()[0])
        same = lambda value: value.replace(origin, "<origin>") if isinstance(value, str) else value
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {table: sorted((tuple(map(same, row)) for row in db.execute(f'SELECT * FROM "{table}"')), key=repr)
                for table in tables}
    finally:
        db.close()


def would_coalesce(io: IO, outbox: list[dict[str, Any]]) -> bool:
    """Whether this drain has a superseded save, whatever the flag says."""
    io.COALESCE_PLAIN_SAVES = True
    try:
        return io.barrier is None and io.alive and bool(io._superseded(outbox))
    finally:
        del io.COALESCE_PLAIN_SAVES


def run(tmp: Path, content: Any, ticks: int) -> tuple[list[dict[str, Any]], Fight]:
    fight = Fight(tmp, content)
    snapshots = []
    for _ in range(ticks):
        fight.tick()
        snapshots.append(fight.snapshot())
    return snapshots, fight


def test_superseded_marks_only_earlier_saves_whose_records_a_later_save_in_the_run_covers(tmp_path, content):
    rig = Rig(tmp_path, content)
    try:
        io = rig.io
        user = rig.clients[0].session.user_id
        player = rig.player()
        save = lambda **extra: {"action": "save", "user": user, "identity": {}, "player": player.record(), **extra}
        other = {"action": "save", "user": "other", "identity": {}, "player": {**player.record(), "id": "p-other"}}
        immediate = save(immediate=True)
        assert io._superseded([save(), other, save()]) == {0}
        assert io._superseded([save(), save(), save()]) == {0, 1}
        # A non-plain effect ends the run this drain processes: nothing after it coalesces.
        assert io._superseded([save(), immediate, save()]) == set()
        assert io._superseded([immediate, save(), save()]) == set()
        # Extra records are covered only by a later save that marks them too.
        assert io._superseded([save(items=[{"id": "i1"}]), save()]) == set()
        assert io._superseded([save(), save(items=[{"id": "i1"}])]) == {0}
        assert io._superseded([save(serial=7), save()]) == set()
        io.COALESCE_PLAIN_SAVES = False
        assert io._superseded([save(), save()]) == set()
    finally:
        rig.store.db.close()


def test_coalescing_leaves_dirty_map_commands_rows_and_world_unchanged(tmp_path, content, reference):
    off, on = reference
    off()
    expected, ref = run(tmp_path / "reference", content, 300)
    on()
    actual, fight = run(tmp_path / "coalesced", content, 300)
    try:
        assert fight.coalesced_drains > 0, "the scripted fight never produced a coalescing drain"
        assert ref.coalesced_drains == 0
        for tick, (want, got) in enumerate(zip(expected, actual)):
            assert got == want, f"dirty map or in-flight commands differ after tick {tick}"
        assert fight.commands() == ref.commands()
        assert fight.world() == ref.world()
        assert database(fight.rig.path) == database(ref.rig.path)
    finally:
        ref.close()
        fight.close()


@pytest.mark.parametrize("point", ["before_pump", "after_pump", "after_flush_submitted", "lost_reply"])
def test_a_writer_death_around_a_coalescing_drain_recovers_the_same_database(tmp_path, content, reference, point):
    """Kill the writer at each step around the first coalescing drain and the
    flush after it; the database a restart reads matches the reference's."""
    off, on = reference
    recovered = []
    for coalesce, name in ((False, "reference"), (True, "coalesced")):
        (on if coalesce else off)()
        fight = Fight(tmp_path / name, content)
        try:
            # Advance to the tick whose drain coalesces; the reference stops at the same tick.
            for _ in range(600):
                fight.game.step()
                if would_coalesce(fight.rig.io, fight.game.core.characters.outbox):
                    break
                fight.cycle()
            else:
                pytest.fail("no coalescing drain in 600 ticks")
            if point == "before_pump":
                # Between the would-be saves and their drain: the server takes its fatal path.
                fight.rig.writer.die()
                with pytest.raises(StorageFailure):
                    fight.cycle(complete=False)
            else:
                fight.cycle(complete=False)
                if point == "after_pump":
                    fight.rig.writer.die()
                else:
                    # Run until the next flush is submitted, then lose it or die before it runs.
                    for _ in range(80):
                        if any(c.kind == "flush" for c, _ in fight.rig.writer.queue):
                            break
                        fight.rig.writer.complete()
                        fight.tick(complete=False)
                    assert any(c.kind == "flush" for c, _ in fight.rig.writer.queue)
                    if point == "lost_reply":
                        fight.rig.writer.complete(lose=True)
                    fight.rig.writer.die()
            recovered.append(database(fight.rig.path))
        finally:
            fight.close()
    assert recovered[0] == recovered[1]
