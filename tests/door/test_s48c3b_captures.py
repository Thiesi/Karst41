"""S48c3b acceptance captures for the presentation lead (#261, #256, #236).

Headless, through the presentation door and the production Core. Each
scene renders at 80x24 and 132x50 in the 256-colour tier, plus one
16-colour frame at 80x24. The same scenes run on main give the `before`
frames in docs/ops/captures/s48c3b (see its README).
"""

from dataclasses import replace
from pathlib import Path
from typing import Callable

import pytest

from karst41.content.loader import load_content
from karst41.door.doorinfo import DoorInfo
from karst41.door.model import Display
from karst41.server.clock import TICK_HZ
from tests.character_helpers import create, place
from tests.fake_terminal import FakeTerminal
from tests.harness import HeadlessServer
from tests.presentation.snapshots import assert_golden

GOLDEN = Path('tests/presentation/golden/s48c3b')
HALL = 'vatside-wake-hall'
CASES = [(80, 24, '256'), (132, 50, '256'), (80, 24, '16')]


def server(repo_root: Path) -> HeadlessServer:
    content = load_content(repo_root/'src/karst41/content')
    return HeadlessServer(content, character_state={})


def caller(game: HeadlessServer, width: int, height: int, handle: str = 'Measure', name: str = 'Measure'):
    client = game.connect({'user_id': 1, 'handle': handle, 'node_name': 'S48c3b'}, width=width, height=height)
    create(game, client, archetype=1, name=name, card='none')
    return client


def shoot(game: HeadlessServer, client, info: DoorInfo, width: int, height: int, tier: str,
          before_render: Callable[[Display, float], None] | None = None) -> FakeTerminal:
    client.session.needs_full_view = True
    game.step(2)
    model = Display(replace(info, width=width, height=height, color_depth=tier), presentation=True)
    now = game.core.clock.now_tick() / TICK_HZ
    for frame in game.frames_for(client):
        model.receive(frame, now)
    model.toast = None
    model.prompt = None  # Creation's name prompt, long answered.
    if before_render:
        before_render(model, now)
    terminal = FakeTerminal(width, height)
    terminal.write(model.render(now + 1)[0])
    return terminal


def pin(request, name: str, terminal: FakeTerminal, width: int, height: int, tier: str) -> None:
    assert_golden(GOLDEN, f's48c3b_{name}_{width}x{height}_{tier}', terminal, tier, trim=True,
                  update=request.config.getoption('--update-goldens'))


def near(game: HeadlessServer, client, zone: str, tile: tuple[int, int]) -> None:
    place(game, client, zone, game.core.world.free_tile(zone, tile))


@pytest.mark.parametrize('width,height,tier', CASES)
def test_row_1_here_at_the_plaza_terminal(info, repo_root, request, width, height, tier):
    game = server(repo_root)
    client = caller(game, width, height)
    place(game, client, 'core-plaza', (31, 6))  # One step south of Public terminal (Plaza 1).
    terminal = shoot(game, client, info, width, height, tier)
    pin(request, 'here', terminal, width, height, tier)


@pytest.mark.parametrize('width,height,tier', CASES)
def test_row_2_nearby_vantongeren(info, repo_root, request, width, height, tier):
    game = server(repo_root)
    client = caller(game, width, height)
    near(game, client, HALL, (14, 8))
    terminal = shoot(game, client, info, width, height, tier)
    pin(request, 'nearby', terminal, width, height, tier)


def hunted(game: HeadlessServer, client) -> None:
    """HUNTED without MARKED: #261's row-3 frame (`s44-hunted-pursuit`)."""
    from karst41.server import truce as truce_rules
    from karst41.server.npcs.templates import PROVOCATION_SECONDS
    player = game.core.characters.players[client.session.player_id]
    now = game.core.combat.now()
    player.keeper_answer = {'keeper': 'sable', 'zone': 'sodium-row', 'until': truce_rules.iso(now + PROVOCATION_SECONDS)}


@pytest.mark.parametrize('width,height,tier', CASES)
def test_row_3_header_zone_with_a_truce_notice(info, repo_root, request, width, height, tier):
    game = server(repo_root)
    client = caller(game, width, height)
    near(game, client, 'sodium-row', (25, 3))
    hunted(game, client)
    def threatened(model: Display, now: float) -> None:
        # A live danger line declares the threat, so the notice takes the header (UI 2.5).
        from karst41.server.protocol import Frame
        model.receive(Frame('log', {'lines': [{'kind': 'danger', 'text': 'Sable is coming for you.'}]}), now)
        model._notice('Fire here and Sable comes. No Halo.', 'log_danger', 8, now)
    terminal = shoot(game, client, info, width, height, tier, threatened)
    pin(request, 'header', terminal, width, height, tier)
    assert 'Fire here and Sable comes.' in terminal.screen_text().splitlines()[0]


@pytest.mark.parametrize('width,height,tier', CASES)
def test_row_4_header_stutter_on_sodium_row(info, repo_root, request, width, height, tier):
    game = server(repo_root)
    client = caller(game, width, height)
    near(game, client, 'sodium-row', (40, 3))
    terminal = shoot(game, client, info, width, height, tier)
    pin(request, 'stutter', terminal, width, height, tier)
    assert 'SODIUM ROW' not in terminal.screen_text().splitlines()[0]


def vantongerens(game: HeadlessServer) -> None:
    """Two hall people whose short forms collide (a fixture rename only)."""
    zone = game.core.world.zones[HALL]
    okafor = next(a for a in zone.actors.values() if a.name == 'Nurse Bea Okafor')
    okafor.name = 'Mr. Kim Vantongeren'


@pytest.mark.parametrize('width,height,tier', CASES)
def test_nearby_collision_falls_back_to_the_prefix(info, repo_root, request, width, height, tier):
    game = server(repo_root)
    client = caller(game, width, height)
    vantongerens(game)
    zone = game.core.world.zones[HALL]
    okafor = next(a for a in zone.actors.values() if a.name == 'Mr. Kim Vantongeren')
    game.core.world.enter(okafor, HALL, game.core.world.free_tile(HALL, (15, 9)))
    near(game, client, HALL, (14, 8))
    terminal = shoot(game, client, info, width, height, tier)
    pin(request, 'collision', terminal, width, height, tier)
    text = terminal.screen_text()
    assert 'Dr. Ilse...' in text and 'Mr. Kim...' in text
    # HERE joins the set (#260) and draws the actor's own glyph.
    assert '☻ Mr. Kim...' in text and '■ Vantongeren' not in text and '☻ Vantongeren' not in text


def test_nearby_collision_changes_as_rows_are_drawn_or_hidden(info, repo_root, request):
    """80x24: with no target both colliding rows are drawn and both use the
    prefix; a target's rows push the farther one off the panel, and the
    nearer, alone again, shows its short form."""
    game = server(repo_root)
    client = caller(game, 80, 24)
    vantongerens(game)
    world, zone = game.core.world, game.core.world.zones[HALL]
    okafor = next(a for a in zone.actors.values() if a.name == 'Mr. Kim Vantongeren')
    vantongeren = next(a for a in zone.actors.values() if a.name == 'Dr. Ilse Vantongeren')
    others = [a for a in zone.actors.values() if a.kind == 'npc' and a not in (okafor, vantongeren)]
    filler, rest = others[0], others[1:]
    near(game, client, HALL, (14, 8))
    me = world.actors[client.session.player_id]
    # Nearest first: the filler, then Vantongeren, then the renamed Okafor;
    # everyone else stands out of sight.
    world.enter(filler, HALL, world.free_tile(HALL, (15, 8)))
    world.enter(vantongeren, HALL, world.free_tile(HALL, (14, 10)))
    world.enter(okafor, HALL, world.free_tile(HALL, (16, 11)))
    for index, other in enumerate(rest):
        world.enter(other, HALL, world.free_tile(HALL, (4 + index, 18)))
    shown = {}
    for case in ('drawn', 'hidden'):
        if case == 'hidden':
            me.target = filler.id  # TARGET's rows leave NEARBY two rows.
        terminal = shoot(game, client, info, 80, 24, '256')
        pin(request, f'collision_{case}', terminal, 80, 24, '256')
        side = compute_side(80, 24)
        shown[case] = '\n'.join(row[side:] for row in terminal.screen_text().splitlines())
    assert 'Dr. Ilse...' in shown['drawn'] and 'Mr. Kim...' in shown['drawn']
    assert 'Mr. Kim' not in shown['hidden'] and '☻ Vantongeren ~' in shown['hidden']


def compute_side(width: int, height: int) -> int:
    from karst41.door.layout import compute
    return compute(width, height).side.x


def test_the_name_row_drops_a_long_handle_at_132x50(info, repo_root, request):
    game = server(repo_root)
    client = caller(game, 132, 50, handle='Measurement-Dept', name='Measure')
    near(game, client, HALL, (20, 14))
    terminal = shoot(game, client, info, 132, 50, '256')
    pin(request, 'handle_dropped', terminal, 132, 50, '256')
    text = terminal.screen_text()
    assert ' Measure ' in text and 'Measure · ' not in text and 'Mea...' not in text


@pytest.mark.parametrize('width,height,tier', CASES)
def test_256_the_log_leaves_rows_blank_rather_than_skip(info, repo_root, request, width, height, tier):
    """Blank rows instead of a tall older entry, with the shorter lines
    older than it hidden: the #256 trade, at both sizes."""
    game = server(repo_root)
    client = caller(game, width, height)
    near(game, client, HALL, (20, 14))
    tall = ' '.join(['Many names vanish into the roll, and the roll keeps none of them for long.'] * 12)

    def lines(model: Display, now: float) -> None:
        from karst41.server.protocol import Frame
        stamp = '2026-11-15T21:00:{:02}+00:00'
        older = [{'kind': 'speech', 'channel': 'zone', 'speaker': 'Vantongeren', 'text': text, 'at': stamp.format(i)}
                 for i, text in enumerate(('Stand up.', 'Slowly.', 'Good.'))]
        model.receive(Frame('log', {'lines': [*older,
            {'kind': 'speech', 'channel': 'zone', 'speaker': 'Vantongeren', 'text': tall, 'at': stamp.format(5)},
            {'kind': 'speech', 'channel': 'zone', 'speaker': 'Vantongeren', 'text': 'Keep your own record.',
             'at': stamp.format(6)}]}), now)
    terminal = shoot(game, client, info, width, height, tier, lines)
    pin(request, 'log_stop', terminal, width, height, tier)
    text = terminal.screen_text()
    assert 'Keep your own record.' in text and 'Good.' not in text
