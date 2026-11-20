"""S48e wording (a): the objective card names places, never ids.

Presentation lead, via the technical lead: zone, object, giver and item
show by display name; an object named for its zone shows once; a field
with no display name is dropped. The targets are the four lines of Vesper's
first job below.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from karst41.content.loader import load_content
from karst41.server.contracts.panels import Panel
from karst41.server.protocol import Frame
from tests.character_helpers import act, place
from tests.contracts.test_first_job import first_job
from tests.harness import HeadlessServer

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope='module')
def content():
    return load_content(ROOT/'src/karst41/content')


def card(game, caller, iid) -> list[str]:
    panels = game.core.contracts.panels
    return panels.card(caller.session, Panel(game.clock.now_tick()), iid)[0]


def test_vesper_first_job_card_lines_match_the_presentation_leads_targets(content):
    game, caller, owner, iid = first_job(ROOT)
    panels = owner.panels
    job = lambda: owner.state.engine.instances[iid]
    first = job().stages[0]
    assert ('Next: '+first.stage.label+'; '+panels.place(first.stage.target)
            == 'Next: Check the parcel hatch; Stairwell C parcel hatch, Block Street')
    assert card(game, caller, iid)[0] == 'Next: Release the parcel hatch; Stairwell C parcel hatch, Block Street'
    owner.actions.perform(caller.session, iid, job().stages[1].targets[0])
    game.step(50)
    game.commit_commerce()
    place(game, caller, 'terraces-blocks', (72, 24))
    owner.actions.perform(caller.session, iid, job().stages[2].targets[0])
    game.commit_commerce()
    lines = card(game, caller, iid)
    assert lines[0] == 'Next: Carry the parcel back to Vesper; The Tin Halo'
    assert 'Missing supply? E reissues at Vesper parcel shelf, Block Street. Bound copies cannot be sold.' in lines
    place(game, caller, 'sodium-tin-halo', (8, 14))
    act(game, caller, Frame('key', {'k': 'e', 't': 0}))
    game.commit_commerce()
    assert job().state == 'complete'
    assert card(game, caller, iid)[0] == "Next: return to Vesper's board."


def test_no_shipped_objective_shows_an_id(content):
    game = HeadlessServer(content, character_state={})
    panels = game.core.contracts.panels
    ids = set(content.zones) | set(content.cores) | set(content.items) | set(content.npcs)
    ids |= {o.id for zone in content.zones.values() for o in zone.meta.objects}
    for job in content.contracts.values():
        for stage in job.stages:
            for target in filter(None, (stage.target, stage.destination)):
                shown = panels.place(target).split(', ')
                assert not ids & set(shown), (job.id, stage.id, shown)
                if target.zone in content.zones or target.core in content.cores or target.cell:
                    assert shown != [''], (job.id, stage.id, target)  # A shipped place always has a name.


def test_a_name_equal_to_its_zone_shows_once_and_a_tile_is_dropped(content):
    panels = HeadlessServer(content, character_state={}).core.contracts.panels
    counter = SimpleNamespace(zone='sodium-tin-halo', object='vesper', tile=(8, 15))
    assert panels.place(counter) == 'The Tin Halo'
    assert panels.place(SimpleNamespace(zone='terraces-blocks', tile=(71, 25))) == 'Block Street'
    assert panels.place(SimpleNamespace(zone='no-such-zone', object='no-such-object')) == ''


@pytest.mark.parametrize('giver, board', [
    ('vesper', "Vesper's board"), ('wardens', "Wardens' board"), ('ferrymen', "Ferrymen's board"),
    ('halvard', "Halvard's board"), ('nobody', 'the board')])
def test_a_givers_board_by_display_name(content, giver, board):
    panels = HeadlessServer(content, character_state={}).core.contracts.panels
    assert panels.board_of(giver) == board


def test_the_journal_card_names_where_the_record_was_made(content):
    game, caller, owner, iid = first_job(ROOT)
    record = owner.state.journal[caller.session.player_id]['stairwell-c-latch-record']
    panel = Panel(game.clock.now_tick(), tab='journal')
    lines = owner.panels.card(caller.session, panel, 'stairwell-c-latch-record')[0]
    where = [line for line in lines if line.startswith('Where: ')]
    assert where == ['Where: Stairwell C parcel hatch, Block Street'], (record['location'], lines)
