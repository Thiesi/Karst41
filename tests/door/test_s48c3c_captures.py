"""S48c3c acceptance captures for the presentation lead: c3c-1's, and
c3c-2's charted gurney frame and help page 1 with a way out in view.

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
    # S48c3c-2 (presentation lead's condition): the first room is charted, so
    # from the gurney the line runs on to the corridor door with no gap. The
    # door cell is charted ground (5.5.5: its class is learned by walking).
    body = model.body
    vx, vy = body['viewport']['x'], body['viewport']['y']
    assert all(body['charted'][14 - vy][x - vx] != -1 for x in range(7, 21))
    assert any('└' + '─' * 12 in row for row in map_rows(terminal, model))


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
    # Sight reaches 10 cells: the line goes on east from the door, but its
    # turn at (37,14) is 17 cells further and not yet in view.
    assert any('+──' in row for row in map_rows(terminal, model))


@pytest.mark.parametrize('width,height,tier', CASES)
def test_the_east_room_shows_the_turn_the_signs_and_the_way_out(info, repo_root, request, width, height, tier):
    game, client = wake(repo_root, width, height)
    place(game, client, HALL, (30, 15))
    terminal, model = frame(game, client, info, width, height, tier)
    pin(request, 'east_room', terminal, width, height, tier)
    rows = '\n'.join(map_rows(terminal, model))
    for seen in ('┐', 'RANGE', 'ISSUE', 'CLINIC ROW →'):
        assert seen in rows, seen


@pytest.mark.parametrize('width,height,tier', CASES)
def test_help_page_one_in_the_hall(info, repo_root, request, width, height, tier):
    # The opening's own state (presentation lead): an unread body, `No. 0001`
    # on day 1, under a plain handle rather than the wide-name fixture.
    plain = replace(info, raw={**info.raw, 'handle': 'Noor'})
    game, client = wake(repo_root, width, height)
    place(game, client, HALL, (6, 6))
    assert game.core.characters.players[client.session.player_id].name == 'No. 0001'
    terminal, _ = frame(game, client, plain, width, height, tier, keys=('?',))
    pin(request, 'help', terminal, width, height, tier)
    text = terminal.screen_text()
    assert 'THIS SCREEN' in text and 'painted line' in text and 'Wide' not in text
    assert 'day 1/90  Under lease: day 1 of 90. No renting or joining yet' in text


@pytest.mark.parametrize('width,height,tier', CASES)
def test_help_page_one_names_a_way_out_in_view(info, repo_root, request, width, height, tier):
    # S48c3c-2 (presentation lead's ask): beside the street door, page 1
    # names the `»` it draws, and never the door object under it as `+`.
    plain = replace(info, raw={**info.raw, 'handle': 'Noor'})
    game, client = wake(repo_root, width, height)
    place(game, client, HALL, (37, 16))
    terminal, _ = frame(game, client, plain, width, height, tier, keys=('?',))
    pin(request, 'help_way_out', terminal, width, height, tier)
    page = terminal.screen_text().split('Page 1/')[0]
    assert '»  way out' in page and 'door inside a place' not in page


def test_the_orderlies_fallback_talk_page(info, repo_root, request):
    # S48c3c-2 (presentation lead's fit and wording check): the fourth visit
    # reads the fallback line, at 80x24 in 256 colours.
    from tests.test_dialogue_runtime import press
    from tests.test_s48c3c2_orderlies import AFTER, orderlies, stand_by, visit
    plain = replace(info, raw={**info.raw, 'handle': 'Noor'})
    game, client = wake(repo_root, 80, 24)
    game.step(30)  # The spawners fill the hall.
    for _ in range(3):
        visit(game, client)
    stand_by(game, client, orderlies(game)[0])
    press(game, client, 'e')
    game.commit_commerce()
    game.step(2)
    assert game.core.dialogue.menus[client.session.id].line == AFTER
    terminal, _ = frame(game, client, plain, 80, 24, '256')
    pin(request, 'orderly_fallback', terminal, 80, 24, '256')
    assert AFTER in terminal.screen_text() and 'Sablier orderly' in terminal.screen_text()


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


@pytest.mark.parametrize('width,height,tier', CASES)
@pytest.mark.parametrize(('zone', 'stand', 'door', 'name'), [
    (HALL, (35, 16), (38, 16), 'way_out_hall'),
    ('sodium-tin-halo', (4, 3), (2, 2), 'way_out_halo')])
def test_a_way_out_is_drawn(info, repo_root, request, width, height, tier, zone, stand, door, name):
    # Terminal UI 2.2 (#265): the street door is `»` once seen; the corridor
    # door and every door into a place stay `+`.
    from karst41.door.present.zone import origin
    content = load_content(repo_root/'src/karst41/content')
    game = HeadlessServer(content, character_state={})
    client = game.connect(width=width, height=height)
    if zone != HALL:
        create(game, client, card='none')
    else:
        game.step(2)
    assert next(ex for ex in content.zones[zone].meta.exits if ex.out).tile == door
    place(game, client, zone, game.core.world.free_tile(zone, stand))
    terminal, model = frame(game, client, info, width, height, tier)
    pin(request, name, terminal, width, height, tier)
    body = model.body
    ox, oy = origin(body, model.layout)
    assert terminal.screen_text().splitlines()[oy + door[1] - body['viewport']['y']][ox + door[0] - body['viewport']['x']] == '»'
