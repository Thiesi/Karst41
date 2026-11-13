"""S48c2: the lesson-hint row captures for the presentation lead (terminal UI 11).

Four frames at each size and tier (24 in all), through the real door:
1. a lesson hint with no context;
2. a lesson hint with an E context;
3. the worst case: the longest hint beside the longest context;
4. the frame just after the hint's key is used (the full row returns).
"""

from dataclasses import replace
from pathlib import Path

import pytest

from karst41.content.loader import load_content
from karst41.door.model import Display
from karst41.door.doorinfo import DoorInfo
from karst41.server.protocol import Frame
from tests.character_helpers import act, place
from tests.fake_terminal import FakeTerminal
from tests.harness import HeadlessServer

HALL = "vatside-wake-hall"
HINT = "Arrow keys walk. Follow the line on the floor."
# Frame 3: beside Vantongeren, the hall's longest E label (talk to Dr. Ilse
# Vantongeren) with the longest hint: at 80x24 the context takes its short form.
FRAMES = (("1_no_context", (6, 5)), ("2_e_context", (19, 14)), ("3_worst", (14, 7)))


@pytest.mark.parametrize("width,height", [(80, 24), (132, 50)])
@pytest.mark.parametrize("tier", ["16", "256", "truecolor"])
def test_s48c2_lesson_hint_row_captures(info: DoorInfo, repo_root: Path, width: int, height: int,
                                        tier: str, golden) -> None:
    content = load_content(repo_root/"src/karst41/content")
    for name, tile in FRAMES:
        game = HeadlessServer(content, character_state={})
        client = game.connect(width=width, height=height)
        model = Display(replace(info, width=width, height=height, color_depth=tier), presentation=True)
        game.step(1)
        place(game, client, HALL, tile)
        client.session.needs_full_view = True
        game.step(2)
        for frame in game.frames_for(client):
            model.receive(frame, 0)
        model.toast = None
        terminal = FakeTerminal(width, height)
        terminal.write(model.render(0)[0])
        rows = terminal.screen_text().splitlines()
        hint_row = rows[model.layout.hint.y]
        assert HINT in hint_row, (name, hint_row)          # Never cut.
        assert "Ctrl-X" not in hint_row and "? help" in hint_row
        if name == "3_worst" and width == 80:
            assert "E talk to Dr...." in hint_row          # The context's short form (drop step 1).
        golden(f"s48c2_hint_{name}_{width}x{height}", terminal, tier, trim=True)
        if name == "1_no_context":
            # Frame 4: the walk key is used once; the full key-help row comes back.
            act(game, client, Frame("key", {"k": "right", "t": 0}))
            for frame in game.frames_for(client):
                model.receive(frame, 1)
            model.toast = None
            terminal.write(model.render(1)[0])  # The door draws only what changed.
            row = terminal.screen_text().splitlines()[model.layout.hint.y]
            assert HINT not in row and "? help" in row
            golden(f"s48c2_hint_4_after_key_{width}x{height}", terminal, tier, trim=True)
