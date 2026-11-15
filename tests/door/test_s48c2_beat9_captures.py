"""S48c2: beat 9 through the presentation door, for the presentation lead.

The record read at the street door with nothing owed (the street-door
order and timing, runner contract amendments 1-5): 9.8, 9.9, the kit
(9.10), the template line (9.10h), the card (9.11).
- 132x50: one frame once 9.11 is in, every line its own entry (terminal
  UI 2.5, canon #251: a script line never merges).
- 80x24: one frame as each line arrives, the newest-row sequence.
Each colour tier has its own name: the text differs by tier (the 16-colour
tier draws walls), so the goldens live outside tests/door/golden.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from karst41.content.loader import load_content
from karst41.door.doorinfo import DoorInfo
from karst41.door.model import Display
from karst41.server.clock import TICK_HZ
from karst41.server.protocol import Frame
from karst41.server.script import GAP_SECONDS
from tests.character_helpers import act, place
from tests.fake_terminal import FakeTerminal
from tests.harness import HeadlessServer
from tests.presentation.snapshots import assert_golden

HALL = "vatside-wake-hall"
DOOR = (37, 16)
GOLDEN = Path("tests/presentation/golden/s48c2-beat9")
BEAT_NINE = ("9.8", "9.9", "9.10", "9.10h", "9.11")


@pytest.mark.parametrize("width,height", [(80, 24), (132, 50)])
@pytest.mark.parametrize("tier", ["16", "256", "truecolor"])
def test_s48c2_beat_nine_captures(info: DoorInfo, repo_root: Path, width: int, height: int, tier: str,
                                  request, caller_provenance) -> None:
    game = HeadlessServer(load_content(repo_root/"src/karst41/content"), character_state={})
    client = game.connect(width=width, height=height)
    model = Display(replace(info, width=width, height=height, color_depth=tier), presentation=True)
    terminal = FakeTerminal(width, height)
    clock = game.core.clock

    def pump() -> list[str]:
        scripts = []
        for frame in game.frames_for(client):
            model.receive(frame, clock.now_tick() / TICK_HZ)
            if frame.type == "log":
                scripts += [line.get("script", "") for line in frame.data["lines"]]
        return scripts

    game.step(1)
    opening = game.core.opening
    state = opening.states[client.session.id]
    record = next(i for i, beat in enumerate(opening.script.beats) if beat.id == "the-record")
    state["fired"] += [ln.id for beat in opening.script.beats[:record] for ln in beat.lines]
    state["queue"] = []  # Earlier beats count as played (a caller who walked through them).
    place(game, client, HALL, DOOR)
    act(game, client, Frame("key", {"k": "e", "t": 0}))
    game.step(10)
    act(game, client, Frame("intent", {"name": "menu_select", "args": {}}))
    for _ in range(600):  # The record's own lines said and the speech gap over.
        voice = client.session.voice_tick
        if not state["queue"] and (voice is None or clock.now_tick() - voice >= GAP_SECONDS * TICK_HZ):
            break
        game.step()
    wake = game.core.characters.wakes[client.session.id]
    act(game, client, Frame("line", {"id": wake.prompt_id, "text": "Noor"}))
    game.step(1)
    game.commit_characters()
    client.session.needs_full_view = True
    seen: list[str] = []
    for _ in range(300):
        game.step()
        arrived = [lid for lid in pump() if lid in BEAT_NINE]
        if not arrived:
            continue
        seen += arrived
        model.toast = None
        terminal.write(model.render(clock.now_tick() / TICK_HZ)[0])
        rows = terminal.screen_text().splitlines()
        log_rows = rows[model.layout.log.y:model.layout.log.bottom]
        if width == 80:
            newest = next(row for row in reversed(log_rows) if row.strip("║ "))
            last_word = opening.text(opening.lines[arrived[-1]][1]).split()[-1]
            assert last_word in newest or "Ctrl-P" in newest, newest  # The line just said ends the log.
            for lid in arrived:
                check(repo_root, request, f"s48c2_beat9_{lid.replace('.', '_')}_{width}x{height}_{tier}", terminal, tier)
        if "9.11" in arrived:
            break
    assert seen == list(BEAT_NINE)  # The street-door order, each line once.
    if width == 132:
        text = "\n".join(log_rows)
        for needle in ("Many names vanish", "Keep your own record", "Issued: sidearm", "200 chits"):
            assert text.count(needle) == 1, needle  # Each its own entry: script lines never merge.
        check(repo_root, request, f"s48c2_beat9_all_{width}x{height}_{tier}", terminal, tier)


def check(repo_root: Path, request, name: str, terminal: FakeTerminal, tier: str) -> None:
    assert_golden(repo_root/GOLDEN, name, terminal, tier, trim=True,
                  update=request.config.getoption("--update-goldens"),
                  packet=request.config.getoption("--review-packet"))
