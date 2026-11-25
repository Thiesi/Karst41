"""#211 acceptance captures for the presentation lead.

Headless, through the presentation door and the production Core, in Clinic
Row with a salvage node placed through Salvage.sync, as ring-fall leaves
one. Each state renders at 80x24 and 132x50 in the 256-colour tier, plus
one 16-colour frame of state 2 at 80x24.
"""

from dataclasses import replace
from pathlib import Path
from random import Random
from typing import Any, Callable

import pytest

from karst41.content.loader import load_content
from karst41.door.doorinfo import DoorInfo
from karst41.door.model import Display
from karst41.server.bus import Event
from karst41.server.clock import TICK_HZ
from karst41.server.protocol import Frame
from tests.character_helpers import create, place
from tests.fake_terminal import FakeTerminal
from tests.harness import HeadlessServer
from tests.presentation.snapshots import assert_golden

GOLDEN = Path('tests/presentation/golden/s211')
ZONE = 'vatside-clinics'
SIZES = [(80, 24, '256'), (132, 50, '256')]
# A fixture objective, injected into the view only for state 3's hint row.
OBJECTIVE = {'next_action': 'Survey the marked loan cabinet', 'progress': '0/1', 'cost': '5s no fee'}


def scene(repo_root: Path, width: int, height: int, me: tuple[int, int], node: tuple[int, int],
          units: int = 3) -> tuple[HeadlessServer, Any]:
    game = HeadlessServer(load_content(repo_root/'src/karst41/content'), character_state={})
    client = game.connect({'user_id': 1, 'handle': 'Picker', 'node_name': 'S211'}, width=width, height=height)
    create(game, client, archetype=1, name='Picker', card='none')
    game.commit_characters()
    game.commit_commerce()
    place(game, client, ZONE, me)
    game.commit_commerce()
    salvage = game.core.salvage
    salvage.state['zones'][ZONE] = {'ring': 1, 'respawn_at': salvage.uptime+600, 'rng': Random(42).getstate(),
        'nodes': {'n211': {'tile': list(node), 'grade': 'hull', 'units': units}}}
    salvage.sync()
    return game, client


def press_e(game: HeadlessServer, client: Any) -> None:
    game.core._echo(Event('input', {'session': client.session, 'frame': Frame('key', {'k': 'e'}),
                                    'tick': game.clock.now_tick()}))


def shoot(game: HeadlessServer, client: Any, info: DoorInfo, width: int, height: int, tier: str,
          before_render: Callable[[Display], None] | None = None, keep_toast: bool = False) -> tuple[FakeTerminal, Display]:
    client.session.needs_full_view = True
    game.step(2)
    model = Display(replace(info, width=width, height=height, color_depth=tier), presentation=True)
    now = game.core.clock.now_tick() / TICK_HZ
    for frame in game.frames_for(client):
        model.receive(frame, now)
    if not keep_toast:
        model.toast = None
    model.prompt = None  # Creation's name prompt, long answered.
    if before_render:
        before_render(model)
    terminal = FakeTerminal(width, height)
    terminal.write(model.render(now + 1)[0])
    return terminal, model


def pin(request: Any, name: str, terminal: FakeTerminal, width: int, height: int, tier: str) -> None:
    assert_golden(GOLDEN, f's211_{name}_{width}x{height}_{tier}', terminal, tier, trim=True,
                  update=request.config.getoption('--update-goldens'))


def rows(terminal: FakeTerminal) -> list[str]:
    return terminal.screen_text().splitlines()


@pytest.mark.parametrize('width,height,tier', SIZES)
def test_state_1_on_the_exit_beside_a_node_the_exit_wins(info, repo_root, request, width, height, tier):
    game, client = scene(repo_root, width, height, (2, 2), (3, 2))
    terminal, _ = shoot(game, client, info, width, height, tier)
    pin(request, '1_exit_beside_node', terminal, width, height, tier)
    assert 'E go to Wake Hall' in rows(terminal)[-2]
    press_e(game, client)
    game.step(TICK_HZ*3)
    assert game.core.characters.sim.avatars[client.session.user_id].zone != ZONE


@pytest.mark.parametrize('width,height,tier', [*SIZES, (80, 24, '16')])
def test_state_2_beside_a_node_full_hint_and_here(info, repo_root, request, width, height, tier):
    game, client = scene(repo_root, width, height, (20, 1), (21, 1))
    terminal, _ = shoot(game, client, info, width, height, tier)
    pin(request, '2_beside_node', terminal, width, height, tier)
    screen = terminal.screen_text()
    assert 'E harvest Hull salvage (3 left)' in rows(terminal)[-2]
    # UI 6 (#261): HERE fits names in 18 cells, so the grade word drops there.
    assert '* salvage (3 left)' in screen


@pytest.mark.parametrize('width,height,tier', SIZES)
def test_state_3_with_an_objective_on_the_row(info, repo_root, request, width, height, tier):
    game, client = scene(repo_root, width, height, (20, 1), (21, 1))
    terminal, _ = shoot(game, client, info, width, height, tier,
                        before_render=lambda model: model.body.update(objective=dict(OBJECTIVE)))
    pin(request, '3_objective', terminal, width, height, tier)
    hint = rows(terminal)[-2]
    if width == 80:
        assert 'Next: Survey the marked loan cabinet' in hint and 'E harvest salvage' in hint
        assert 'Hull salvage (3 left)' not in hint
    else:
        # 132x50 shows the objective in the panel; the hint keeps the full name.
        assert 'E harvest Hull salvage (3 left)' in hint


@pytest.mark.parametrize('width,height,tier', SIZES)
def test_state_4_the_count_drops_live_after_one_harvest(info, repo_root, request, width, height, tier):
    game, client = scene(repo_root, width, height, (20, 1), (21, 1))
    press_e(game, client)
    channel = game.core.fabrication.channels[client.session.player_id]
    for _ in range(200):
        game.step()
        game.commit_characters()
        game.commit_commerce()
        if game.core.salvage.state['zones'][ZONE]['nodes']['n211']['units'] == 2:
            break
    game.core.fabrication.stop(client.session, "You stop working. Completed harvest units stay with you.")
    game.commit_commerce()
    assert channel.kind == 'harvest'
    terminal, _ = shoot(game, client, info, width, height, tier)
    pin(request, '4_after_harvest', terminal, width, height, tier)
    assert 'E harvest Hull salvage (2 left)' in rows(terminal)[-2]
    assert '* salvage (2 left)' in terminal.screen_text()


@pytest.mark.parametrize('width,height,tier', SIZES)
def test_state_5_beside_an_empty_node_no_focus_and_no_toast(info, repo_root, request, width, height, tier):
    game, client = scene(repo_root, width, height, (20, 1), (21, 1), units=0)
    game.step(2)
    game.frames_for(client)  # Drop the login notices, so only E's own answer could show.
    press_e(game, client)
    terminal, model = shoot(game, client, info, width, height, tier, keep_toast=True)
    pin(request, '5_empty_node', terminal, width, height, tier)
    screen = terminal.screen_text()
    assert 'harvest' not in screen and 'salvage' not in screen.lower()
    assert model.toast is None or 'salvage' not in str(model.toast[0]).lower()
    assert 'n211' not in screen  # An internal id is never a display label (architecture 5.5.2).
