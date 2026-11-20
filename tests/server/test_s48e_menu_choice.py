"""S48e: a menu choice survives a view that moved only in what it is not about.

The native evening found it: Enter on the Hall terminal's training room was
dropped. Every menu_select carries the view revision it was made on, and
each handler refused it unless that was still the session's revision. While
the room menu was open its document republished every tenth of a second as
the rig's Pick cooldown counted down (`me.rig[0].cd`), so a key in flight
across one of those ticks was silently lost; the door does not resend it.

The guard now asks whether the choice still means what it meant
(Session.fresh): the same menu on the same terms, which is everything the
menu shows (rows and their cells, detail with prices, totals and failure
odds, footer, confirmation, title, cursor, actions) except the caller's
status block (`me`) and the countdowns sessions.CHOICE_COUNTDOWNS names
(technical lead). Changed terms still refuse the stale input, and a view
that offers no choice (the zone, the Lattice map) keeps the exact rule.
"""

import pytest

from karst41.server.protocol import Frame
from karst41.server.sessions import CHOICE_HISTORY, choice_state
from tests.harness import HeadlessServer
from tests.test_s48e_evening import Caller, Walk

NL = '\n'


def menu(cursor: int = 0, rows=('one', 'two'), cooldown: float = 0.0, cells=None, detail: str = '') -> dict:
    return {'title': 'ROOM', 'cursor': cursor, 'detail': detail,
            'rows': [{'id': row, 'cells': list(cells.get(row, [row])) if cells else [row]} for row in rows],
            'actions': [{'key': 'enter', 'label': 'select'}], 'me': {'rig': [{'name': 'Pick', 'cd': cooldown}]}}


def published(session, tick: int, body: dict, kind: str = 'menu') -> int:
    assert session.publish_view(Frame('view', {'rev': session.view_revision+1, 'kind': kind, 'body': body}), None, tick)
    return session.view_revision


def session_of():
    return HeadlessServer().connect().session


def test_a_choice_outlives_a_cooldown_tick_but_not_a_changed_menu():
    session = session_of()
    seen = published(session, 1, menu(cooldown=0.4))
    published(session, 2, menu(cooldown=0.3))  # Only the caller's status (`me`) moved.
    assert session.view_revision != seen and session.fresh(seen)
    published(session, 3, menu(cursor=1))  # The cursor moved: another choice.
    assert not session.fresh(seen)
    published(session, 4, menu(rows=('one', 'three')))  # The rows changed.
    assert not session.fresh(seen) and session.fresh(session.view_revision)


TERMS = [
    # Technical lead: a stale market purchase or allocation spends nothing
    # (architecture), and costs show before the player commits (terminal UI).
    ('a market row price, same id',
     menu(cells={'one': ['Patch', '12 ch']}), menu(cells={'one': ['Patch', '15 ch']})),
    ('a clinic TOTAL, same row',
     menu(detail=NL.join(['Fitting 80 ch', 'TOTAL 480 ch', 'Failure 2%'])),
     menu(detail=NL.join(['Fitting 80 ch', 'TOTAL 520 ch', 'Failure 2%']))),
    ('a board reward, same job',
     menu(detail=NL.join(['Your reward: 200 chits; 100 XP', 'Deadline: 3540 s'])),
     menu(detail=NL.join(['Your reward: 220 chits; 100 XP', 'Deadline: 3540 s']))),
    ("a stage's terms, same job",
     menu(detail=NL.join(['Next: Collect the bound supply', 'Action: 0 s; No fee.'])),
     menu(detail=NL.join(['Next: Collect the bound supply', 'Action: 3 s; 20 ch fee.']))),
]


@pytest.mark.parametrize('case, before, after', TERMS, ids=[case for case, _, _ in TERMS])
def test_changed_terms_refuse_the_stale_choice(case, before, after):
    session = session_of()
    seen = published(session, 1, before)
    published(session, 2, {**before, 'me': {'rig': [{'name': 'Pick', 'cd': 0.1}]}})  # Status only: still fresh.
    assert session.fresh(seen), case
    published(session, 3, after)
    assert not session.fresh(seen), case


def test_named_countdowns_and_the_lattice_status_line_do_not_change_the_choice():
    session = session_of()
    card = NL.join(['Your reward: 200 chits', 'Deadline: {}', 'Field window: {} remaining. Failure returns.'])
    seen = published(session, 1, menu(detail=card.format('3540 s', '120 s')))
    published(session, 2, menu(detail=card.format('3539 s', '119 s')))
    assert session.fresh(seen)
    room = NL.join(['HP {}/58  INT 100  TRACE 0', 'Select ICE to inspect and target.'])
    seen = published(session, 3, menu(detail=room.format(43)))
    published(session, 4, menu(detail=room.format(44)))
    assert session.fresh(seen)
    published(session, 5, menu(detail=NL.join(['HP 44/58  INT 100  TRACE 0', 'Select data to lift it.'])))
    assert not session.fresh(seen)


def test_a_view_with_no_choice_keeps_the_exact_revision_rule():
    session = session_of()
    seen = published(session, 1, {'tiles': [], 'me': {'hp': 10}}, kind='zone')
    published(session, 2, {'tiles': [], 'me': {'hp': 11}}, kind='zone')
    assert choice_state(Frame('view', {'kind': 'zone', 'body': {}})) is None
    assert not session.fresh(seen) and not session.fresh(None) and not session.fresh('1')


def test_only_recent_revisions_are_remembered():
    session = session_of()
    first = published(session, 1, menu())
    for tick in range(2, CHOICE_HISTORY+3):
        published(session, tick, menu(cooldown=tick/10))
    assert len(session.choices) == CHOICE_HISTORY and not session.fresh(first)


def test_enter_on_the_training_room_lifts_after_the_cooldown_ticked():
    walk = Walk()
    caller = Caller(walk.game, 1, 'Noor', 80, 24, walk.seen)
    walk.callers.append(caller)
    walk.tick(5)
    walk.drone(caller, skip=False)
    lat, pid = walk.game.core.lattice, caller.session.player_id
    walk.walk(caller, (35, 15), adjacent=True)
    walk.tick(5)
    caller.press('j')
    walk.tick(10)
    caller.press('tab')
    walk.tick()
    for _ in range(20):
        presence = lat.presences[pid]
        defenders = lat.encounters.defenders(presence)
        if not defenders or not defenders[0].alive:
            break
        caller.press('1')
        walk.tick()
        presence = lat.presences[pid]
        walk.tick(max(1, presence.cast.until-walk.game.clock.now_tick()+3) if presence.cast else 10)
    caller.press('e')
    walk.tick()
    caller.drain()
    door = caller.models['present']
    seen = door.revision  # What the door's Enter will carry.
    rows = door.body.get('rows') or []
    assert rows and rows[0]['id'], door.body
    walk.tick(2)  # The rig's cooldown ticks: the document republishes.
    assert caller.session.view_revision != seen
    frame = Frame('intent', {'name': 'menu_select', 'args': {'row': rows[0]['id'], 'key': 'enter'},
                             'request_id': 's48e-lift', 'expected_revision': seen})
    walk.game.core.receive(caller.session, frame)
    walk.tick(45)
    assert walk.game.core.characters.wakes[caller.session.id].lessons.get('lifted')
