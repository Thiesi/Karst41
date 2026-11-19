"""S48c3c-2: the orderlies' talk page, one line per visit, in order.

first-ten-minutes §4.11 and the technical lead's ruling: the condition
`visits_below N` (dramatis personae Part III) reads the caller's committed
count of talk-page opens for this talker. The count advances on each open,
only when the line reaches the caller, and stops at the talker's cap (the
largest N its lines use), so visit 4 and later write nothing. An unread
body's count is held in the wake record and carried into the character.
"""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from karst41.content.loader import load_content
from karst41.content.validate import validate
from karst41.server.clock import TICK_HZ
from karst41.server.wake import Wake
from tests.character_helpers import create, place, state
from tests.harness import HeadlessServer
from tests.test_dialogue_runtime import press

HALL, TALKER = 'vatside-wake-hall', 'sablier-orderly'
O1 = "Line's on the floor. It only goes one way."
O2 = "She'll talk whether you stand there or not. Walk."
O3 = "Gurney's yours till the next one. That's about an hour."
AFTER = "Four today. Vat three's slow again."


@pytest.fixture(scope='module')
def shipped():
    return load_content(Path(__file__).parents[1]/'src/karst41/content')


@pytest.fixture
def content(shipped):
    return deepcopy(shipped)


def orderlies(game):
    zone = game.core.world.zones[HALL]
    return sorted((a for a in zone.actors.values() if a.behaviour.get('template') == 'npc-sablier-orderly'),
                  key=lambda a: a.id)


def hall(content, *, created=False, user=1):
    game = HeadlessServer(content, character_state={})
    caller = game.connect({'user_id': user, 'handle': 'Orderly', 'node_name': 'S48c3c'})
    game.step(2)
    if created:
        create(game, caller, card='none')
    place(game, caller, HALL, (6, 5))
    game.step(30)  # The spawners fill the hall.
    assert len(orderlies(game)) == 2
    return game, caller


def stand_by(game, caller, actor):
    # Hold the orderlies still: a wander between presses would move the
    # contact out of reach. Their posts are the P3 matrix's scene.
    zone = game.core.world.zones[HALL]
    for other, tile in zip(orderlies(game), ((10, 4), (13, 3))):
        game.core.world.enter(other, HALL, tile)
    x, y = actor.position
    place(game, caller, HALL, (x, y+1))
    assert zone.passable((x, y+1))


def visit(game, caller, actor=None) -> str | None:
    """One visit: E beside an orderly, the page's line, then B closes it."""
    stand_by(game, caller, actor or orderlies(game)[0])
    press(game, caller, 'e')
    game.commit_commerce()
    game.step(2)
    menu = game.core.dialogue.menus.get(caller.session.id)
    line = menu.line if menu and menu.contact.talker == TALKER else None
    press(game, caller, 'b')
    game.step(2)
    assert caller.session.id not in game.core.dialogue.menus
    return line


def player(game, caller):
    return game.core.characters.players[caller.session.player_id]


def settles(game, monkeypatch):
    calls = []
    original = game.core.commerce.settle

    def counted(target, changes, *args, **kwargs):
        calls.append(deepcopy(changes))
        return original(target, changes, *args, **kwargs)
    monkeypatch.setattr(game.core.commerce, 'settle', counted)
    return calls


def test_the_record_is_bound_to_the_orderlies_with_no_offers(shipped):
    assert shipped.npcs['npc-sablier-orderly'].dialogue == TALKER
    talker = shipped.dialogue[TALKER]
    assert [line.text for line in talker.lines] == [O1, O2, O3, AFTER]
    assert [line.when for line in talker.lines] == [('visits_below 1',), ('visits_below 2',), ('visits_below 3',), ()]
    assert talker.offers == () and [bark.text for bark in talker.barks] == [AFTER]
    assert talker.barks[0].min_interval_seconds == 90  # P8's default interval.
    assert shipped.active('dialogue', TALKER)


def test_a_created_character_hears_o1_o2_o3_then_the_fallback_and_visit_four_writes_nothing(content, monkeypatch):
    game, caller = hall(content, created=True)
    calls = settles(game, monkeypatch)
    assert [visit(game, caller) for _ in range(3)] == [O1, O2, O3]
    assert player(game, caller).progress['talk_visits'] == {TALKER: 3}
    assert [c['progress'].get('talk_visits') for c in calls] == [{TALKER: 1}, {TALKER: 2}, {TALKER: 3}]
    assert TALKER in calls[0]['progress']['met_talkers']  # Visit 1: the meeting and the count, one settle.
    calls.clear()
    assert [visit(game, caller) for _ in range(2)] == [AFTER, AFTER]
    assert calls == [] and player(game, caller).progress['talk_visits'] == {TALKER: 3}


def test_an_unread_body_hears_them_in_order_held_in_the_wake_record(content):
    game, caller = hall(content)
    assert game.core.characters.unread(caller.session.player_id)
    assert [visit(game, caller) for _ in range(5)] == [O1, O2, O3, AFTER, AFTER]
    wake = game.core.characters.wakes[caller.session.id]
    assert wake.visits == {TALKER: 3}
    assert game.core.characters.identities[caller.session.user_id]['wake']['visits'] == {TALKER: 3}


def test_the_count_is_shared_by_both_orderlies(content):
    game, caller = hall(content, created=True)
    first, second = orderlies(game)
    assert [visit(game, caller, first), visit(game, caller, second), visit(game, caller, first),
            visit(game, caller, second)] == [O1, O2, O3, AFTER]


def test_a_talker_that_does_not_count_writes_nothing_new(content, monkeypatch):
    game, caller = hall(content, created=True)
    okafor = next(a for a in game.core.world.zones[HALL].actors.values()
                  if a.behaviour.get('template') == 'npc-cast-okafor')
    calls = settles(game, monkeypatch)
    for _ in range(2):
        x, y = okafor.position
        place(game, caller, HALL, next(t for t in ((x-1, y), (x, y-1), (x+1, y), (x, y+1))
                                       if game.core.world.zones[HALL].passable(t)
                                       and not any(a.position == t for a in game.core.world.zones[HALL].actors.values())))
        press(game, caller, 'e')
        game.commit_commerce()
        game.step(2)
        assert game.core.dialogue.menus[caller.session.id].contact.talker == 'okafor'
        press(game, caller, 'b')
        game.step(2)
    assert len(calls) == 1 and 'talk_visits' not in calls[0]['progress']  # The first meeting only, as before.
    assert 'talk_visits' not in player(game, caller).progress


def test_the_count_survives_a_restart_for_a_created_character(content):
    game, caller = hall(content, created=True)
    assert [visit(game, caller) for _ in range(2)] == [O1, O2]
    game.commit_characters()
    game.core.sessions.disconnect(caller.session)
    back_game = HeadlessServer(game.core.loop.content, character_state=state(game))
    back = back_game.connect({'user_id': 1, 'handle': 'Orderly', 'node_name': 'S48c3c'})
    place(back_game, back, HALL, (6, 5))
    back_game.step(30)
    assert player(back_game, back).progress['talk_visits'] == {TALKER: 2}
    assert visit(back_game, back) == O3


def test_the_count_survives_a_restart_for_an_unread_body_and_carries_into_the_character(content):
    game, caller = hall(content)
    assert [visit(game, caller) for _ in range(2)] == [O1, O2]
    game.commit_characters()
    game.core.sessions.disconnect(caller.session)
    back_game = HeadlessServer(game.core.loop.content, character_state=state(game))
    back = back_game.connect({'user_id': 1, 'handle': 'Orderly', 'node_name': 'S48c3c'})
    back_game.step(2)
    assert back_game.core.characters.unread(back.session.player_id)
    assert back_game.core.characters.wakes[back.session.id].visits == {TALKER: 2}
    assert player(back_game, back).progress['talk_visits'] == {TALKER: 2}
    create(back_game, back, card='none')
    assert player(back_game, back).progress['talk_visits'] == {TALKER: 2}  # Carried at creation.
    place(back_game, back, HALL, (6, 5))
    back_game.step(30)
    assert [visit(back_game, back) for _ in range(2)] == [O3, AFTER]


def test_an_old_save_without_the_key_reads_as_no_visits(content):
    saved = Wake().saved()
    saved.pop('visits')
    assert Wake.restore(saved).visits == {}
    game, caller = hall(content, created=True)
    player(game, caller).progress.pop('talk_visits', None)
    assert visit(game, caller) == O1


def test_a_failed_show_restores_the_count(content, monkeypatch):
    game, caller = hall(content, created=True)
    assert visit(game, caller) == O1
    dialogue = game.core.dialogue
    monkeypatch.setattr(dialogue, 'show', lambda *args, **kwargs: False)
    stand_by(game, caller, orderlies(game)[0])
    press(game, caller, 'e')
    game.commit_commerce()  # The count's write commits, the show fails, the restore is queued...
    game.step(2)
    game.commit_commerce()  # ...and commits.
    progress = player(game, caller).progress
    assert progress['talk_visits'] == {TALKER: 1} and TALKER in progress['met_talkers']
    monkeypatch.undo()
    assert visit(game, caller) == O2


@pytest.mark.parametrize('argument', ['0', '10', 'x', '1.5', '-1', '٣'])
def test_the_validator_rejects_a_bad_n(content, argument):
    talker = content.dialogue[TALKER]
    content.dialogue[TALKER] = replace(talker, lines=(replace(talker.lines[0], when=(f'visits_below {argument}',)),
                                                      *talker.lines[1:]))
    assert any('visits_below' in p.message and 'from 1 to 9' in p.message for p in validate(content))


def test_the_validator_rejects_visits_below_on_an_ask_answer(content):
    from karst41.content.schema import Offer
    talker = content.dialogue[TALKER]
    content.dialogue[TALKER] = replace(talker, offers=(Offer(kind='ask', label='Ask about the line', hotkey='a',
                                                             ref='o2', asks='Where does it go?'),))
    assert any("ask's answer line cannot use visits_below" in p.message for p in validate(content))


def test_the_bark_goes_through_the_one_emitter(content, monkeypatch):
    game, caller = hall(content, created=True)
    first = orderlies(game)[0]
    stand_by(game, caller, first)
    ambience = game.core.dialogue.ambience
    heard = []
    original = ambience.sim.speak

    def spoken(session, actor, text, **kwargs):
        heard.append((actor.behaviour.get('template'), text, kwargs.get('replay')))
        return original(session, actor, text, **kwargs)
    monkeypatch.setattr(ambience.sim, 'speak', spoken)
    caller.session.voice_tick = None
    game.step(95 * TICK_HZ)
    assert ('npc-sablier-orderly', AFTER, False) in heard  # P8's emitter speaks with replay=False.
    assert any(key in ambience.zones[HALL].barks.next_due for key in (a.id for a in orderlies(game)))
