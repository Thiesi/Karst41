"""#277 (D5-GC): the production start-up path freezes the start-up heap.

- `freeze_startup_heap()` collects once and freezes what survives.
- `python -m karst41.server` builds its Server with `freeze_heap=True`, and
  `Server.start()` freezes after the world is open and before the tick task
  starts. A Server built without the flag (tests, harnesses) never freezes.
- Freezing changes no simulation outcome: the deterministic pursuit replay
  (scripts/bench_277.py) ends in the same world with and without it.

The interpreter starts with a few objects frozen already (377 on CPython
3.12), and gc.unfreeze() would release those too. So the tests that freeze
the real heap run in a child process, and this process keeps its collector
exactly as it was.
"""

from __future__ import annotations

import asyncio
import gc
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from karst41.server import __main__ as entry
from karst41.server import app as app_module
from karst41.server.app import Server


ROOT = Path(__file__).parents[2]


@pytest.fixture
def unfrozen():
    """This process's collector is never frozen further by these tests."""
    before = gc.get_freeze_count()
    yield
    assert gc.get_freeze_count() == before


def child(code: str, *args: str) -> Any:
    """Run `code` in a fresh interpreter with this checkout's import paths; it prints one JSON line."""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT)])}
    done = subprocess.run([sys.executable, "-c", code, *args], capture_output=True, text=True, env=env, cwd=ROOT,
                          timeout=900)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_freeze_startup_heap_freezes_the_live_heap(unfrozen) -> None:
    result = child("""
import gc, json
from karst41.server.heap import freeze_startup_heap
import karst41.content.loader  # Something sizeable on the heap.
before, tracked = gc.get_freeze_count(), len(gc.get_objects())
frozen = freeze_startup_heap()
print(json.dumps([before, tracked, frozen, gc.get_freeze_count(), len(gc.get_objects())]))
""")
    before, tracked, frozen, count, left = result
    assert frozen == count > before
    assert left < tracked // 10, "the frozen objects left the collector's generations"


def test_the_production_entry_point_builds_the_server_with_the_freeze(monkeypatch, scratch_dir: Path) -> None:
    built: list[dict[str, Any]] = []

    class Recorded:
        exit_code = 0

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            built.append(kwargs)

        async def run(self) -> None:
            return None

        def request_stop(self) -> None:
            return None
    monkeypatch.setattr(entry, "Server", Recorded)
    assert entry.main(["--install-dir", str(scratch_dir)]) == 0
    assert built and built[0].get("freeze_heap") is True


@pytest.mark.parametrize("freeze_heap", [True, False])
def test_start_freezes_after_the_world_opens_and_before_the_first_tick(monkeypatch, scratch_dir: Path,
                                                                          unfrozen, freeze_heap: bool) -> None:
    events: list[str] = []
    monkeypatch.setattr(app_module, "freeze_startup_heap", lambda: events.append("freeze") or 1)
    task = Server._task

    def recorded(server: Server, coroutine: Any) -> Any:
        events.append(getattr(coroutine, "__name__", "task"))
        return task(server, coroutine)
    monkeypatch.setattr(Server, "_task", recorded)

    async def scenario() -> None:
        server = Server(scratch_dir, tcp=True, characters=False, freeze_heap=freeze_heap)
        await server.start()
        try:
            assert server.core is not None  # The world was built before the freeze.
        finally:
            await server.stop()
    asyncio.run(scenario())
    assert "_ticks" in events
    if freeze_heap:
        assert events.index("freeze") < events.index("_ticks"), events
        log = (scratch_dir / "logs" / "server.log").read_text(encoding="utf-8")
        assert "startup heap frozen" in log
    else:
        assert "freeze" not in events


REPLAY = """
import gc, json, sys, tempfile
from pathlib import Path
from scripts import bench_277 as bench
from karst41.content.loader import load_content
from karst41.server.heap import freeze_startup_heap

freeze = sys.argv[1] == "freeze"
content = load_content(Path("src/karst41/content"))  # The child runs in the checkout root.
content.walls = None
with tempfile.TemporaryDirectory() as tmp:
    rig, caller = bench.world(Path(tmp), content)
    game = rig.game
    for _ in range(40):
        game.step()
        bench.cycle(rig)
    if freeze:
        freeze_startup_heap()
    zone = game.core.world.zones["sodium-row"]
    me = game.core.characters.sim.avatars[caller.session.user_id].actor
    guards = [a for a in zone.actors.values() if a.behaviour.get("template") == "npc-sable-enforcer"]
    game.core.npcs.provoked(me, min(guards, key=lambda a: ((a.x - me.x) ** 2 + (a.y - me.y) ** 2, a.id)))
    for _ in range(300):
        if me.health < 40:
            me.health = 130
        game.step()
        bench.cycle(rig)
        gc.collect()  # Full collections throughout, so the frozen heap's exemption is exercised.
    result = bench.fingerprint(game)
    rig.store.db.close()
print(json.dumps({"fingerprint": result, "frozen": gc.get_freeze_count()}))
"""


def replay(freeze: bool) -> dict[str, Any]:
    """The bench_277 pursuit in a child process: same content, seed and inputs; optionally frozen after start-up."""
    return child(REPLAY, "freeze" if freeze else "plain")


def test_the_replay_ends_in_the_same_world_with_the_heap_frozen(unfrozen) -> None:
    plain, frozen = replay(freeze=False), replay(freeze=True)
    assert frozen["frozen"] > plain["frozen"] + 10000, "the frozen arm froze the replay's world"
    assert frozen["fingerprint"] == plain["fingerprint"]
