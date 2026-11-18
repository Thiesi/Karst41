"""S48c3c-1 acceptance captures for the presentation lead.

Headless, through the presentation door and the production Core, a Wake
before the record (an unread body) in the hall:
- the gurney frame: the hall drawn as a place, `0 ch  day 1/90`, no grade;
- beside the corridor door: the painted line meets the door from both sides;
- the first frame after the door: the line goes on toward the street door;
- help page 1 (This screen);
and, after creation, the street marks on Clinic Row and Meridian Plaza.
80x24 and 132x50 in the 256-colour tier, plus 16 colours at 80x24.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from karst41.content.loader import load_content
from karst41.door.model import Display
from karst41.server.clock import TICK_HZ
from tests.character_helpers import create, place
from tests.fake_terminal import FakeTerminal
from tests.harness import HeadlessServer
from tests.presentation.snapshots import assert_golden

GOLDEN = Path('tests/presentation/golden/s48c3c')
HALL = 'vatside-wake-hall'
CASES = [(80, 24, '256'), (132, 50, '256'), (80, 24, '16')]


def frame(game: HeadlessServer, client, info, width: int, height: int, tier: str, keys: tuple[str, ...] = ()):
    client.session.needs_full_view = True
    game.step(2)
    model = Display(replace(info, width=width, height=height, color_depth=tier), presentation=True)
    now = game.core.clock.now_tick() / TICK_HZ
    for f in game.frames_for(client):
        model.receive(f, now)
    model.toast = model.prompt = None
    for key in keys:
        model.key(key, now)
    terminal = FakeTerminal(width, height)
    terminal.write(model.render(now + 1)[0])
    return terminal, model


def pin(request, name: str, terminal: FakeTerminal, width: int, height: int, tier: str) -> None:
    assert_golden(GOLDEN, f's48c3c_{name}_{width}x{height}_{tier}', terminal, tier, trim=True,
                  update=request.config.getoption('--update-goldens'))


def wake(repo_root: Path, width: int, height: int):
    game = HeadlessServer(load_content(repo_root/'src/karst41/content'), character_state={})
    client = game.connect(width=width, height=height)
    game.step(2)
    return game, client


def map_rows(terminal: FakeTerminal, model: Display) -> list[str]:
    area = model.layout.viewport
    return [row[area.x:area.right] for row in terminal.screen_text().splitlines()[area.y:area.bottom]]


@pytest.mark.parametrize('width,height,tier', CASES)
def test_the_gurney_frame(info, repo_root, request, width, height, tier):
    game, client = wake(repo_root, width, height)
    terminal, model = frame(game, client, info, width, height, tier)
    pin(request, 'gurney', terminal, width, height, tier)
    text = terminal.screen_text()
    assert 'DECANT' in text and '0 ch     day 1/90' in text and 'No. ' in text
    assert '│' in '\n'.join(map_rows(terminal, model))  # The line starts beside the gurney.


@pytest.mark.parametrize('width,height,tier', CASES)
def test_the_line_meets_the_corridor_door_from_both_sides(info, repo_root, request, width, height, tier):
    game, client = wake(repo_root, width, height)
    place(game, client, HALL, (18, 13))
    terminal, model = frame(game, client, info, width, height, tier)
    pin(request, 'corridor_door', terminal, width, height, tier)
    assert any('─+─' in row for row in map_rows(terminal, model))  # No gap either side of the door.


@pytest.mark.parametrize('width,height,tier', CASES)
def test_the_first_frame_after_the_corridor_door(info, repo_root, request, width, height, tier):
    game, client = wake(repo_root, width, height)
    place(game, client, HALL, (21, 13))
    terminal, model = frame(game, client, info, width, height, tier)
    pin(request, 'after_door', terminal, width, height, tier)
    rows = '\n'.join(map_rows(terminal, model))
    assert '┐' in rows or '└' in rows  # The line's turn toward the street door is in view.


@pytest.mark.parametrize('width,height,tier', CASES)
def test_help_page_one_in_the_hall(info, repo_root, request, width, height, tier):
    game, client = wake(repo_root, width, height)
    place(game, client, HALL, (6, 6))
    terminal, _ = frame(game, client, info, width, height, tier, keys=('?',))
    pin(request, 'help', terminal, width, height, tier)
    text = terminal.screen_text()
    assert 'THIS SCREEN' in text and 'painted line' in text and 'Signatory: you may rent and join' in text


@pytest.mark.parametrize('width,height,tier', CASES)
@pytest.mark.parametrize(('zone', 'tile', 'mark'), [('vatside-clinics', (3, 3), 'MERIDIAN PLAZA →'),
                                                    ('core-plaza', (87, 3), '← NEON STAIR'),
                                                    ('core-plaza', (58, 7), 'NEON STAIR ↓')])
def test_the_street_marks(info, repo_root, request, width, height, tier, zone, tile, mark):
    game = HeadlessServer(load_content(repo_root/'src/karst41/content'), character_state={})
    client = game.connect(width=width, height=height)
    create(game, client, card='none')
    place(game, client, zone, game.core.world.free_tile(zone, tile))
    terminal, model = frame(game, client, info, width, height, tier)
    name = {'MERIDIAN PLAZA →': 'clinics_mark', '← NEON STAIR': 'plaza_fingerpost', 'NEON STAIR ↓': 'plaza_turn'}[mark]
    pin(request, name, terminal, width, height, tier)
    assert mark in '\n'.join(map_rows(terminal, model))
