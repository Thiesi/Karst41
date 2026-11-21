"""S48e wording (c) and walk finding 3: E names the item a stage moves.

Presentation lead, via the technical lead: at Vesper's counter with the
parcel, the focus reads "E hand over Stuck-door Package"; at the shelf it
reads "E take Stuck-door Package"; UI 11's short form keeps the last word
("E hand over Package") where the whole name does not fit. One helper serves
both verbs. Same target, tile and outcome as before.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from karst41.content.loader import load_content
from karst41.door.chrome import short_name
from karst41.door.doorinfo import DoorInfo
from karst41.door.model import Display
from karst41.server.clock import TICK_HZ
from karst41.server.protocol import Frame
from tests.character_helpers import act, create, place
from tests.fake_terminal import FakeTerminal
from tests.harness import HeadlessServer

ROOT = Path(__file__).parents[1]


def at_the_counter_with_the_parcel(width: int, height: int, stop_at_the_shelf: bool = False):
    content = load_content(ROOT/'src/karst41/content')
    content.zones = {k: replace(z, meta=replace(z.meta, spawners=())) for k, z in content.zones.items()}
    game = HeadlessServer(content, character_state={})
    raw = {'user_id': 1, 'handle': 'Courier', 'node_name': 'S48e'}
    caller = game.connect(raw, width=width, height=height)
    create(game, caller, archetype=0, card='none')
    owner, pid = game.core.contracts, caller.session.player_id
    owner.game.players[pid].progress['wake_combat'] = 'skipped'
    place(game, caller, 'sodium-tin-halo', (8, 14))
    proposal = owner.propose(pid, owner.offer('vesper-first-thing', pid, 'vesper'), (pid,))
    owner.consent(proposal.id, pid)
    owner.start(proposal.id, pid)
    game.commit_commerce()
    iid = next(iid for iid, j in owner.state.engine.instances.items() if j.template == 'vesper-first-thing')
    job = lambda: owner.state.engine.instances[iid]
    place(game, caller, 'terraces-blocks', (71, 25))
    for index in (0, 1):
        owner.actions.perform(caller.session, iid, job().stages[index].targets[0])
        game.step(50)
        game.commit_commerce()
    place(game, caller, 'terraces-blocks', (72, 24))
    if stop_at_the_shelf:
        game.step(2)
        return game, caller, raw, job
    owner.actions.perform(caller.session, iid, job().stages[2].targets[0])
    game.commit_commerce()
    place(game, caller, 'sodium-tin-halo', (8, 14))
    game.step(2)
    return game, caller, raw, job


def screen(game, caller, raw, width, height, tmp_path) -> str:
    info = DoorInfo(tmp_path/'door_info.json', tmp_path, raw, width, height, '256', 3600)
    model, terminal = Display(info, presentation=True), FakeTerminal(width, height)
    now = game.clock.now_tick()/TICK_HZ
    game.frames_for(caller)  # The direct creation path's prompts; start from a full view.
    caller.session.needs_full_view = True
    game.step()
    for frame in game.frames_for(caller):
        model.receive(frame, now)
    out, _ = model.render(now)
    terminal.write(out)
    return terminal.screen_text()


@pytest.mark.parametrize('width, height, shown', [
    (80, 24, 'E hand over Package'), (132, 50, 'E hand over Stuck-door Package')])
def test_the_delivery_focus_hands_the_parcel_over_by_name(tmp_path, width, height, shown):
    game, caller, raw, job = at_the_counter_with_the_parcel(width, height)
    view = game.core.characters.sim.views[caller.session.id].previous['body']
    assert view['focus'] == {'id': 'vesper', 'label': 'Stuck-door Package', 'verb': 'hand over'}
    text = screen(game, caller, raw, width, height, tmp_path)
    assert shown in text, text
    act(game, caller, Frame('key', {'k': 'e', 't': 0}))
    game.commit_commerce()
    assert job().state == 'complete'  # The same outcome as before.


@pytest.mark.parametrize('width, height, shown', [
    (80, 24, 'E take Stuck-door Package'), (132, 50, 'E take Stuck-door Package')])
def test_the_pickup_takes_the_parcel_by_name(tmp_path, width, height, shown):
    # Presentation lead, walk finding 3: a pickup names the item it gives,
    # not the shelf. At the shelf the whole name fits both sizes; where it
    # does not, UI 11's short form is "E take Package" (pinned below).
    assert short_name('Stuck-door Package') == 'Package'
    game, caller, raw, job = at_the_counter_with_the_parcel(width, height, stop_at_the_shelf=True)
    view = game.core.characters.sim.views[caller.session.id].previous['body']
    assert view['focus'] == {'id': 'stairwell-c-parcel', 'label': 'Stuck-door Package', 'verb': 'take'}
    text = screen(game, caller, raw, width, height, tmp_path)
    assert shown in text, text
    act(game, caller, Frame('key', {'k': 'e', 't': 0}))
    game.commit_commerce()
    assert job().index == 3  # The same outcome as before.


def test_a_generated_pickup_names_its_item():
    game = HeadlessServer(load_content(ROOT/'src/karst41/content'), character_state={})
    actions, content = game.core.contracts.actions, game.core.contracts.content
    for jid in ('vesper-local-plant', 'vesper-local-fetch'):
        stage = next(s for s in content.contracts[jid].stages if s.completion == 'carried')
        actor = type('A', (), {'zone': stage.target.zone})()
        choice = actions.target_interaction(actor, stage.target, stage.verb, stage)
        assert choice.focus.verb == 'take' and choice.focus.label == content.items[stage.target.item].name, jid


def test_a_pickup_still_takes():
    game = HeadlessServer(load_content(ROOT/'src/karst41/content'), character_state={})
    actions = game.core.contracts.actions
    job = game.core.contracts.content.contracts['vesper-first-thing']
    collect = next(stage for stage in job.stages if stage.id == 'collect-parcel')
    actor = type('A', (), {'zone': 'terraces-blocks'})()
    choice = actions.target_interaction(actor, collect.target, collect.verb, collect)
    assert choice.focus.verb == 'take'
