"""S48e: a menu choice survives a view that moved only in what it is not about.

The native evening found it: Enter on the Hall terminal's training room was
dropped. Every menu_select carries the view revision it was made on, and
each handler refused it unless that was still the session's revision. While
the room menu was open its document republished every tenth of a second as
the rig's Pick cooldown counted down (`me.rig[0].cd`), so a key in flight
across one of those ticks was silently lost; the door does not resend it.
The guard now asks whether the choice still means what it meant
(Session.fresh): the same title, rows, cursor and actions. A menu whose
choice changed still refuses the stale input, and a view that offers no
choice (the zone, the Lattice map) keeps the exact revision rule.
"""

from pathlib import Path

from karst41.content.loader import load_content
from karst41.server.protocol import Frame
from karst41.server.sessions import CHOICE_HISTORY, choice_state
from tests.harness import HeadlessServer
from tests.test_s48e_evening import Caller, Walk

ROOT = Path(__file__).parents[2]


def menu(cursor: int = 0, rows=('one', 'two'), cooldown: float = 0.0) -> dict:
    return {'title': 'ROOM', 'rows': [{'id': row, 'cells': [row]} for row in rows], 'cursor': cursor,
            'actions': [{'key': 'enter', 'label': 'select'}], 'me': {'rig': [{'name': 'Pick', 'cd': cooldown}]}}


def published(session, tick: int, body: dict, kind: str = 'menu') -> int:
    assert session.publish_view(Frame('view', {'rev': session.view_revision+1, 'kind': kind, 'body': body}), None, tick)
    return session.view_revision


def test_a_choice_outlives_a_cooldown_tick_but_not_a_changed_menu():
    game = HeadlessServer()
    session = game.connect().session
    seen = published(session, 1, menu(cooldown=0.4))
    published(session, 2, menu(cooldown=0.3))  # Only the cooldown moved.
    assert session.view_revision != seen and session.fresh(seen)
    published(session, 3, menu(cursor=1))  # The cursor moved: another choice.
    assert not session.fresh(seen)
    published(session, 4, menu(rows=('one', 'three')))  # The rows changed.
    assert not session.fresh(seen) and session.fresh(session.view_revision)


def test_a_view_with_no_choice_keeps_the_exact_revision_rule():
    game = HeadlessServer()
    session = game.connect().session
    seen = published(session, 1, {'tiles': [], 'me': {'hp': 10}}, kind='zone')
    published(session, 2, {'tiles': [], 'me': {'hp': 11}}, kind='zone')
    assert choice_state(Frame('view', {'kind': 'zone', 'body': {}})) is None
    assert not session.fresh(seen) and not session.fresh(None) and not session.fresh('1')


def test_only_recent_revisions_are_remembered():
    game = HeadlessServer()
    session = game.connect().session
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
