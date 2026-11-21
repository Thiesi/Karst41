"""S48e finding 2: a stage at the talker's counter, done from their board.

Technical lead's ruling (option A): on a board opened through a talker-reach
grant (finding 1), an active stage the caller holds whose target is that
talker's bound counter gets a board action. It is the same contract action
on the same code path, with its reach taken from the grant; Marked, the
roster and the stage order are unchanged. The cursor starts on that job.
The word is the presentation lead's, by stage, at both sizes.
"""

from itertools import count
from pathlib import Path
from types import SimpleNamespace

import pytest

from karst41.content.loader import load_content
from karst41.server.contracts.panels import Panel, granted_label
from karst41.server.protocol import Frame
from tests.character_helpers import act, place
from tests.test_s48e_talker_reach import HALO, VESPER, at, hold, talk, toasts

REQUESTS = count(1)


@pytest.fixture(scope='module')
def content():
    return load_content(Path(__file__).parents[1]/'src/karst41/content')


def select(game, client, key: str) -> None:
    """A board key as the door sends it: menu_select on the cursor's row."""
    panels, sid = game.core.contracts.panels, client.session.id
    body = panels.body(client.session, panels.menus[sid])
    act(game, client, Frame('intent', {
        'name': 'menu_select', 'args': {'row': body['rows'][body['cursor']]['id'], 'key': key},
        'request_id': f's48e-{next(REQUESTS)}', 'expected_revision': client.session.view_revision}))


def actions(game, client) -> list[dict]:
    panels, sid = game.core.contracts.panels, client.session.id
    return panels.body(client.session, panels.menus[sid])['actions']


def carrying_the_parcel(content):
    """Vesper's first job taken from (8,13), worked up to its delivery stage."""
    game, client, vesper = at(content, HALO, VESPER, (9, 14), (8, 13))
    owner, pid = game.core.contracts, client.session.player_id
    owner.game.players[pid].progress['wake_combat'] = 'skipped'
    talk(game, client)
    for key in 'cay':
        act(game, client, Frame('key', {'k': key, 't': 0}))
    game.commit_commerce()
    iid = next(iid for iid, j in owner.state.engine.instances.items() if j.template == 'vesper-first-thing')
    job = lambda: owner.state.engine.instances[iid]
    place(game, client, 'terraces-blocks', (71, 25))
    for index in (0, 1):
        owner.actions.perform(client.session, iid, job().stages[index].targets[0])
        game.step(50)
        game.commit_commerce()
    place(game, client, 'terraces-blocks', (72, 24))
    owner.actions.perform(client.session, iid, job().stages[2].targets[0])
    game.commit_commerce()
    stage = job().stages[job().index]
    assert (stage.stage.verb, stage.stage.completion, stage.targets[0].object) == ('fetch', 'delivered', 'vesper')
    place(game, client, HALO, (8, 13))
    hold(game, vesper, (9, 14))
    game.step(2)
    return game, client, iid, job


def test_from_8_13_the_board_hands_the_parcel_over_and_takes_the_turn_in(content):
    game, client, iid, job = carrying_the_parcel(content)
    owner, sid = game.core.contracts, client.session.id
    player = owner.game.players[client.session.player_id]
    talk(game, client)
    act(game, client, Frame('key', {'k': 'c', 't': 0}))
    panel = owner.panels.menus[sid]
    assert owner.panels.rows(client.session, panel)[panel.cursor]['id'] == iid  # The cursor starts on it.
    assert actions(game, client)[0] == {'key': 'e', 'label': 'hand over'}
    for width, height in ((80, 24), (132, 50)):
        client.session.caps.update(width=width, height=height)
        assert {'key': 'e', 'label': 'hand over'} in actions(game, client)
    select(game, client, 'e')
    game.commit_commerce()
    assert job().state == 'complete', toasts(game, client)
    assert all(a['key'] != 'e' for a in actions(game, client))
    chits = player.chits_hand
    select(game, client, 't')
    select(game, client, 'y')
    game.commit_commerce()
    assert job().state == 'turned_in'
    assert player.chits_hand-chits == (220 if player.archetype == 'operator' else 200)


def test_a_board_used_directly_offers_no_board_action(content):
    game, client, iid, job = carrying_the_parcel(content)
    owner, sid = game.core.contracts, client.session.id
    place(game, client, HALO, (8, 14))  # Beside the counter; no talk page, so no grant.
    # The board opened at the counter itself, as test_first_job opens it.
    owner.panels.menus[sid] = Panel(game.clock.now_tick(), target='vesper',
                                    handler=owner.panels.station(client.session, 'vesper'))
    assert sid not in game.core.commerce.reach
    index = job().index
    assert all(a['key'] != 'e' for a in actions(game, client))
    select(game, client, 'e')
    game.commit_commerce()
    assert job().index == index and job().state == 'active'


def test_a_stage_at_another_object_offers_no_board_action(content):
    game, client, vesper = at(content, HALO, VESPER, (9, 14), (8, 13))
    owner, pid, sid = game.core.contracts, client.session.player_id, client.session.id
    owner.game.players[pid].progress['wake_combat'] = 'skipped'
    talk(game, client)
    for key in 'cay':
        act(game, client, Frame('key', {'k': key, 't': 0}))
    game.commit_commerce()
    iid = next(iid for iid, j in owner.state.engine.instances.items() if j.template == 'vesper-first-thing')
    assert owner.state.engine.instances[iid].stages[0].targets[0].object == 'stairwell-c-parcel-hatch'
    act(game, client, Frame('intent', {'name': 'menu_close', 'args': {}}))
    talk(game, client)
    act(game, client, Frame('key', {'k': 'c', 't': 0}))
    assert sid in game.core.commerce.reach
    rows = owner.panels.rows(client.session, owner.panels.menus[sid])
    owner.panels.menus[sid].cursor = [r['id'] for r in rows].index(iid)
    assert all(a['key'] != 'e' for a in actions(game, client))
    select(game, client, 'e')
    game.commit_commerce()
    assert owner.state.engine.instances[iid].index == 0


def test_stepping_off_the_grant_tile_ends_the_board_action(content):
    game, client, iid, job = carrying_the_parcel(content)
    owner, sid = game.core.contracts, client.session.id
    talk(game, client)
    act(game, client, Frame('key', {'k': 'c', 't': 0}))
    assert actions(game, client)[0]['key'] == 'e'
    place(game, client, HALO, (7, 12))
    place(game, client, HALO, (8, 13))
    assert sid not in game.core.commerce.reach
    index = job().index
    if sid in owner.panels.menus:  # A board left open is out of reach.
        with pytest.raises(ValueError, match='Move next to the counter or cache.'):
            owner.panels.body(client.session, owner.panels.menus[sid])
        select(game, client, 'e')
        game.commit_commerce()
    assert job().index == index and job().state == 'active'


@pytest.mark.parametrize('verb, completion, word', [
    ('fetch', 'delivered', 'hand over'), ('fetch', 'carried', 'take'),
    ('survey', 'observed', 'inspect'), ('survey', 'recorded', 'inspect'), ('survey', 'compared', 'inspect'),
    ('plant', 'planted', 'plant'), ('hack', 'flipped', None), ('clear', 'defeated', None),
    ('escort', 'arrived', None)])
def test_the_board_actions_word_by_stage(verb, completion, word):
    assert granted_label(SimpleNamespace(verb=verb, completion=completion)) == word


def test_a_timed_stage_from_the_board_finishes_after_the_board_closes(content, monkeypatch):
    # Automated review (7032993434 and later): a channel started through the
    # grant must not need the board left open; it still ends if the caller moves.
    game, client, iid, job = carrying_the_parcel(content)
    owner, sid = game.core.contracts, client.session.id
    monkeypatch.setattr(type(owner), 'duration', lambda self, player, stage: 3.0)  # A timed hand-over.
    talk(game, client)
    act(game, client, Frame('key', {'k': 'c', 't': 0}))
    assert actions(game, client)[0] == {'key': 'e', 'label': 'hand over'}
    select(game, client, 'e')
    assert sid in owner.actions.channels and job().state == 'active'
    # Esc while the bar fills cancels the channel first ("Esc/B cancels"),
    # not the board; the board can still close under it another way (its
    # timeout, or a refusal when its body is redrawn).
    owner.panels.close(client.session)
    assert sid not in owner.panels.menus and sid not in game.core.commerce.reach
    game.step(40)
    game.commit_commerce()
    assert job().state == 'complete', toasts(game, client)


def test_esc_while_a_board_stage_channels_cancels_the_channel_first(content, monkeypatch):
    game, client, iid, job = carrying_the_parcel(content)
    owner, sid = game.core.contracts, client.session.id
    monkeypatch.setattr(type(owner), 'duration', lambda self, player, stage: 3.0)
    talk(game, client)
    act(game, client, Frame('key', {'k': 'c', 't': 0}))
    select(game, client, 'e')
    act(game, client, Frame('intent', {'name': 'menu_close', 'args': {}}))
    game.step()
    assert sid not in owner.actions.channels and sid in owner.panels.menus


def test_a_timed_stage_from_the_board_still_ends_when_the_caller_moves(content, monkeypatch):
    game, client, iid, job = carrying_the_parcel(content)
    owner, sid = game.core.contracts, client.session.id
    monkeypatch.setattr(type(owner), 'duration', lambda self, player, stage: 3.0)
    talk(game, client)
    act(game, client, Frame('key', {'k': 'c', 't': 0}))
    select(game, client, 'e')
    assert sid in owner.actions.channels
    place(game, client, HALO, (7, 12))
    game.step(40)
    game.commit_commerce()
    assert sid not in owner.actions.channels and job().state == 'active'
