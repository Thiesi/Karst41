"""#211: a salvage node beside an exit, its harvest focus, and the empty node.

Game design 5.6 and 8.4, architecture 5.5.2: an exit underfoot outranks an
adjacent salvage node for E; a node with units names its harvest live; an
empty node is not an E target. Focus and dispatch come from one resolver.
"""

from pathlib import Path
from random import Random
from typing import Any

import pytest

from karst41.content.loader import ContentTree, load_content
from karst41.door import chrome
from karst41.server.bus import Event
from karst41.server.protocol import Frame
from tests.character_helpers import create, place
from tests.harness import FakeClient, HeadlessServer
from tests.presentation.exit_underfoot_matrix import execute
from tests.presentation.test_hint_labels import focus_context

ZONE = 'vatside-clinics'
EXIT = {'id': 'to-wake-hall', 'label': 'Wake Hall', 'verb': 'go to'}
NODE = {'id': 'n211', 'label': 'Hull salvage (3 left)', 'verb': 'harvest', 'short': 'Hull (3 left)'}

# The #211 table: caller tile, node tile (None: no node), node units, then the
# expected E focus and the route that both predicts and consumes E.
CASES = {
    'no node': ((2, 2), None, 3, EXIT, 'characters'),
    'node diagonal to the exit tile': ((2, 2), (3, 1), 3, EXIT, 'characters'),
    'node orthogonal to the exit tile': ((2, 2), (3, 2), 3, EXIT, 'characters'),
    'node two tiles away': ((2, 2), (4, 2), 3, EXIT, 'characters'),
    'node beside, no exit near': ((10, 3), (11, 3), 3, NODE, 'fabrication'),
    'node underfoot': ((10, 3), (10, 3), 3, NODE, 'fabrication'),
    'empty node beside the exit': ((2, 2), (3, 2), 0, EXIT, 'characters'),
}


@pytest.fixture(scope='module')
def content() -> ContentTree:
    return load_content(Path(__file__).resolve().parents[2]/'src/karst41/content')


def scene(content: ContentTree, me: tuple[int, int], node: tuple[int, int] | None,
          units: int, grade: str = 'hull') -> tuple[HeadlessServer, FakeClient]:
    game = HeadlessServer(content, character_state={})
    caller = game.connect()
    create(game, caller, archetype=0, name='Salvager', card='none')
    game.commit_characters()
    game.commit_commerce()
    place(game, caller, ZONE, me)
    game.commit_commerce()
    if node is not None:
        salvage = game.core.salvage
        salvage.state['zones'][ZONE] = {'ring': 1, 'respawn_at': salvage.uptime+600, 'rng': Random(42).getstate(),
            'nodes': {'n211': {'tile': list(node), 'grade': grade, 'units': units}}}
        salvage.sync()
    return game, caller


@pytest.mark.parametrize('case', CASES)
def test_211_exit_underfoot_outranks_an_adjacent_node_and_focus_matches_dispatch(
        content: ContentTree, case: str) -> None:
    me, node, units, focus, route = CASES[case]
    game, caller = scene(content, me, node, units)
    actual = execute(game, caller, Frame('key', {'k': 'e'}))
    assert actual['focus'][0] == focus
    assert actual['predicted'] == actual['outcome']['handler'] == route
    channel = game.core.fabrication.channels.get(caller.session.player_id)
    if route == 'characters':
        assert actual['outcome']['travel'] == 'to-wake-hall' or actual['outcome']['body'][0] != ZONE
        assert channel is None
    else:
        assert channel is not None and channel.kind == 'harvest' and channel.object == 'n211'
    # The empty node no longer answers E with its refusal.
    assert not any('salvage node' in text for text in actual['outcome']['logs'])
    assert not any('n211' in text for text in actual['outcome']['logs'])  # Never the internal id.
    assert not any('salvage node' in str(frame.data) for frame in caller.session.send_queue)


def test_211_label_uses_the_grade_item_name_and_counts_down_live(content: ContentTree) -> None:
    game, caller = scene(content, (10, 3), (11, 3), 3, grade='optics')
    focus, _ = game.core.interaction_views(caller.session)
    assert focus == {'id': 'n211', 'label': 'Optics salvage (3 left)', 'verb': 'harvest', 'short': 'Optics (3 left)'}
    game.core._echo(Event('input', {'session': caller.session, 'frame': Frame('key', {'k': 'e'}),
                                    'tick': game.clock.now_tick()}))
    assert game.core.fabrication.channels[caller.session.player_id].kind == 'harvest'
    for _ in range(200):
        game.step()
        game.commit_characters()
        game.commit_commerce()
        units = game.core.salvage.state['zones'][ZONE]['nodes'].get('n211', {}).get('units')
        if units == 2:
            break
    assert units == 2
    focus, _ = game.core.interaction_views(caller.session)
    assert focus == {'id': 'n211', 'label': 'Optics salvage (2 left)', 'verb': 'harvest', 'short': 'Optics (2 left)'}


def test_211_hint_row_full_and_short_form(content: ContentTree) -> None:
    context = focus_context(NODE, None, 'J')
    raw = context[0][0]+' '+context[0][1]
    assert raw == 'E harvest Hull salvage (3 left)'
    assert chrome.short_name(NODE['label']) == 'salvage'
    assert chrome.focus_hint(raw, 78, context, None, fit_all=True).startswith('E harvest Hull salvage (3 left)')
    # With the objective on the row at 80x24, UI 11 shortens E's name; nothing clips.
    objective = {'next_action': 'Survey the marked loan cabinet', 'progress': '0/1', 'cost': '5s no fee'}
    row = chrome.focus_hint(raw+'  Next: legacy', 78, context, objective)
    assert row == 'Next: Survey the marked loan cabinet 0/1 | 5s no fee  E harvest salvage'


def test_211_here_names_the_node_with_its_glyph(content: ContentTree) -> None:
    from karst41.door.present.zone import at_hand
    game, caller = scene(content, (10, 3), (11, 3), 3)
    game.step()
    body = game.core.loop.views.view(caller.session)['body'] if hasattr(game.core.loop, 'views') else None
    if body is None:
        frames = game.frames_for(caller)
        body = next(f.data['body'] for f in reversed(frames) if f.type == 'view' and f.data.get('kind') == 'zone')
    assert body['focus'] == NODE
    assert '* Hull salvage (3 left)' in at_hand(body)


@pytest.mark.parametrize('node', [(3, 1), (3, 2)])
def test_211_s24_travel_step_passes_with_a_node_beside_the_exit(content: ContentTree, node: tuple[int, int]) -> None:
    """The acceptance travel helper (accept_s10_netbbs.py) on a real view, with
    the node #211 caught beside Clinic Row's west exit: it must not see `{}`."""
    import asyncio
    from types import SimpleNamespace as NS
    from tests.presentation.test_native_navigation import helpers
    game, caller = scene(content, (6, 4), node, 3)
    core, session = game.core, caller.session
    sim = core.characters.sim
    avatar = sim.avatars[session.user_id]

    def advance() -> None:
        game.step()
        game.commit_characters()
        game.commit_commerce()
        for frame in game.frames_for(caller):
            if frame.type in ('view', 'delta'):
                caller.send(Frame('ack', {'rev': frame.data['rev']}))

    async def walk(connection: Any, target: tuple[int, int]) -> None:
        place(game, caller, ZONE, target)
        game.commit_commerce()

    async def wait(condition: Any, timeout: float = 10) -> None:
        for _ in range(int(timeout*10)):
            if condition():
                return
            advance()
        raise TimeoutError

    async def write(key: bytes) -> None:
        assert key == b'e'
        core._echo(Event('input', {'session': session, 'frame': Frame('key', {'k': 'e'}),
                                   'tick': game.clock.now_tick()}))

    probe = helpers(asyncio=asyncio, wait=wait)()
    probe.walk = walk
    probe.game = NS(sim=sim)
    probe.service = NS(world=core.world, clock=game.clock)
    connection = NS(avatar=avatar, session=session, caller=NS(write=write, screen=NS(
        screen_text=lambda: core.world.zones[avatar.zone].template.meta.name+'\n')))
    assert core.world.zones[ZONE].objects['n211'].state['units'] == 3
    asyncio.run(probe.travel(connection, 'vatside-wake-hall'))
    assert avatar.zone == 'vatside-wake-hall'


@pytest.mark.parametrize('change', ['duplicate', 'missing'])
def test_211_every_salvage_grade_has_exactly_one_item(change: str) -> None:
    """The harvest focus names the grade's item; the validator makes a missing
    or ambiguous name impossible instead of a silent null focus."""
    from copy import deepcopy
    from dataclasses import replace
    from karst41.content.validate import validate
    tree = load_content(Path(__file__).resolve().parents[2]/'src/karst41/content')
    assert not [p for p in validate(tree) if 'salvage grade' in p.message]
    tree = deepcopy(tree)
    hull = tree.items['slv_hull']
    if change == 'duplicate':
        tree.items['slv_hull_copy'] = replace(hull, id='slv_hull_copy', name='Hull scrap')
    else:
        tree.items['slv_hull'] = replace(hull, salvage=None)
    messages = [p.message for p in validate(tree) if 'salvage grade' in p.message]
    assert messages == [f"salvage grade hull needs exactly one item template, found {2 if change == 'duplicate' else 0}"]
