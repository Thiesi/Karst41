"""S48c3c-1, terminal UI 2.2 and architecture 5.4 (#265): a way out is drawn `»`.

An exit flagged `out` (the Wake Hall's, the Tin Halo's and the Terraces
lobby's way back to the street) is listed in the view's `outs` only when the
same view discloses its cell as seen now or remembered. Its tile and
interaction are unchanged: E still uses the door.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from karst41.content.loader import load_content
from karst41.content.validate import validate
from karst41.door.model import Display
from karst41.door.present.zone import screen_legend
from karst41.server.clock import TICK_HZ
from karst41.server.protocol import Frame
from tests.character_helpers import act, place
from tests.fake_terminal import FakeTerminal
from tests.harness import HeadlessServer

HALL, DOOR = 'vatside-wake-hall', (38, 16)


def wake(content, width=80, height=24):
    game = HeadlessServer(content, character_state={})
    client = game.connect(width=width, height=height)
    game.step(2)
    return game, client


def sent(game, client) -> dict:
    client.session.needs_full_view = True
    game.step(2)
    return game.core.characters.sim.views[client.session.id].previous['body']


@pytest.fixture
def content(repo_root: Path):
    return load_content(repo_root/'src/karst41/content')


def test_the_three_ways_back_to_the_street_are_flagged_and_nothing_else(content):
    flagged = {(zid, ex.id) for zid, zone in content.zones.items() for ex in zone.meta.exits if ex.out}
    assert flagged == {('vatside-wake-hall', 'to-clinics'), ('sodium-tin-halo', 'to-sodium-row'),
                       ('terraces-lobby', 'to-blocks')}
    assert validate(content) == []


def test_an_out_exit_is_never_hidden(content):
    hall = content.zones[HALL]
    exits = tuple(replace(ex, hidden=True) if ex.id == 'to-clinics' else ex for ex in hall.meta.exits)
    content.zones[HALL] = replace(hall, meta=replace(hall.meta, exits=exits))
    assert any('never hidden' in p.message for p in validate(content))


def test_outs_list_only_seen_or_remembered_cells(content):
    game, client = wake(content)
    assert 'outs' not in sent(game, client)  # From the gurney the street door is out of sight.
    place(game, client, HALL, (36, 16))
    assert sent(game, client)['outs'] == [list(DOOR)]  # In sight.
    place(game, client, HALL, (30, 13))
    body = sent(game, client)
    x, y = DOOR[0] - body['viewport']['x'], DOOR[1] - body['viewport']['y']
    if 0 <= y < len(body['tiles']) and 0 <= x < len(body['tiles'][y]):
        assert body['tiles'][y][x] in (-2,) or body['tiles'][y][x] >= 0
        assert body['outs'] == [list(DOOR)]  # Remembered (or still seen) and in the viewport.


def test_a_withheld_charted_only_or_hidden_exit_is_never_in_outs(content):
    hall = content.zones[HALL]
    # Charted ground around the door, never seen: ground only, never `outs`.
    from karst41.content.schema import Region
    content.zones[HALL] = replace(hall, meta=replace(hall.meta, charted=True, public=(Region(20, 11, 20, 9),)))
    game, client = wake(content)
    place(game, client, HALL, (22, 13))
    body = sent(game, client)
    assert 'outs' not in body or list(DOOR) not in body['outs']
    # A hidden exit is never listed, even beside the caller.
    exits = tuple(replace(ex, hidden=True) if ex.id == 'to-clinics' else ex for ex in hall.meta.exits)
    content.zones[HALL] = replace(hall, meta=replace(hall.meta, exits=exits))
    game, client = wake(content)
    place(game, client, HALL, (37, 16))
    assert 'outs' not in sent(game, client)


def test_e_on_the_street_door_still_uses_the_door(content):
    # A created character (an unread body's E here opens the record, S48c1).
    from tests.character_helpers import create
    game, client = wake(content)
    create(game, client, card='none')
    place(game, client, HALL, DOOR)
    avatar = game.core.characters.sim.avatars[client.session.user_id]
    for _ in range(40):
        act(game, client, Frame('key', {'k': 'e', 't': 0}))
        game.step(2)
        if avatar.actor.zone != HALL:
            break
    assert avatar.actor.zone == 'vatside-clinics'


@pytest.mark.parametrize('width,height', [(80, 24), (132, 50)])
@pytest.mark.parametrize('tier', ['16', '256', 'truecolor'])
def test_the_street_door_draws_a_way_out_at_every_tier(info, content, width, height, tier):
    game, client = wake(content, width, height)
    place(game, client, HALL, (35, 16))
    client.session.needs_full_view = True
    game.step(2)
    model = Display(replace(info, width=width, height=height, color_depth=tier), presentation=True)
    now = game.core.clock.now_tick() / TICK_HZ
    for f in game.frames_for(client):
        model.receive(f, now)
    model.toast = model.prompt = None
    terminal = FakeTerminal(width, height)
    terminal.write(model.render(now + 1)[0])
    body = model.body
    from karst41.door.present.zone import origin
    ox, oy = origin(body, model.layout)
    x, y = ox + DOOR[0] - body['viewport']['x'], oy + DOOR[1] - body['viewport']['y']
    assert terminal.screen_text().splitlines()[y][x] == '»'
    assert ('»', 'way out') in screen_legend(body)
