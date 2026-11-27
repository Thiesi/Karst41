"""#246: a choice on a Lattice room menu survives its own countdowns ticking.

S48e (Session.fresh) lets a menu choice through when the newer view offers
the same choice on the same terms, ignoring the caller's status block and
the countdowns sessions.CHOICE_COUNTDOWNS names. Two room-menu cells count
down by themselves and were not named:

- a flipped control's reset, "<state> / 42s remaining" (Interlock.control_row);
- a crew data offer's turn, "Crew turn: Noor (12s)." (Crews.offer_label).

While either is on screen the choice digest changes every second, so an
Enter whose round trip spans a second boundary is refused. S14 gate-duo's
choose() then waits for a menu that never closes (#246), and a caller on a
slow link loses the key in the same way. Only the seconds are blanked: the
latch state and the crew-turn holder stay terms of the choice.
"""

from types import SimpleNamespace

from karst41.server.clock import TICK_HZ
from karst41.server.crew_engine import Crews
from karst41.server.interlock import Interlock
from karst41.server.protocol import Frame
from karst41.server.sessions import _without_countdowns, choice_state
from tests.lattice.test_encounters import enter
from tests.lattice.test_engine import intent, runners  # noqa: F401  (fixture)
from tests.lattice.test_interlock import control_room, flip


def room_view(*cells: list[str]) -> Frame:
    rows = [{'id': f'row-{index}', 'cells': list(row)} for index, row in enumerate(cells)]
    return Frame('view', {'kind': 'menu', 'body': {'title': 'ROOM', 'cursor': 0, 'rows': rows,
                                                   'detail': 'HP 58/58  INT 100  TRACE 0\nSelect ICE to inspect and target.',
                                                   'actions': [{'key': 'enter', 'label': 'select'}]}})


def latch_row(state: str, reset_in: int | None) -> list[str]:
    """The control row exactly as Interlock.control_row builds it."""
    interlock = SimpleNamespace(control_view=lambda core, control: {'state': state, 'reset_in': reset_in})
    control = SimpleNamespace(id='gate-nine-service-latch', label='Service latch', reset_seconds=60)
    return Interlock.control_row(interlock, 'lat-gatehouse-east', control)['cells']


def crew_cell(holder: str, remaining: float) -> str:
    """The crew-turn cell exactly as Crews.offer_label builds it."""
    crews = SimpleNamespace(clock=SimpleNamespace(now_utc=lambda: SimpleNamespace(timestamp=lambda: 1000.0)),
                            name=lambda pid: pid)
    return Crews.offer_label(crews, {'mode': 'round_robin', 'recipient': holder, 'until': 1000.0+remaining})


def test_the_latch_reset_countdown_is_not_a_term_of_the_choice():
    assert latch_row('on', 42)[1] == 'on / 42s remaining'
    assert choice_state(room_view(latch_row('on', 42))) == choice_state(room_view(latch_row('on', 41)))
    # The latch's state is a term: a flip now would do something else.
    assert choice_state(room_view(latch_row('on', 42))) != choice_state(room_view(latch_row('off', 42)))
    assert choice_state(room_view(latch_row('on', 42))) != choice_state(room_view(latch_row('on', None)))


def test_the_crew_turn_countdown_is_not_a_term_of_the_choice_but_its_holder_is():
    data = ['Cash cache', '120 chits', 'ready']
    assert crew_cell('Noor', 12) == 'Crew turn: Noor (12s).'
    assert choice_state(room_view([*data, crew_cell('Noor', 12)])) == choice_state(room_view([*data, crew_cell('Noor', 11)]))
    assert choice_state(room_view([*data, crew_cell('Noor', 12)])) != choice_state(room_view([*data, crew_cell('Ada', 12)]))
    assert choice_state(room_view([*data, crew_cell('Noor', 12)])) != choice_state(room_view(data))  # The turn ended.


BLANKED_BEFORE_246 = [
    # Each S48e countdown, in the text the server builds, and exactly what it
    # was blanked to before #246 added `keep`: the whole match becomes the
    # pattern's own text. The new entries must not shift any of them.
    ('Deadline: 3540 s', r'Deadline: \d+ s'),
    ('Field window: 120 s remaining. Failure returns to the listed launch checkpoint.',
     r'Field window: \d+ s remaining. Failure returns to the listed launch checkpoint.'),
    ('NPC restoration in 42 s', r'NPC restoration in \d+ s'),
    ('Reservation: 30 s', r'Reservation: \d+ s'),
    ('Expiry: 900 s', r'Expiry: \d+ s'),
    ('HP 44/58  INT 100  TRACE 0\nSelect ICE to inspect and target.',
     r'^HP [\d.]+/[\d.]+  INT \S+  TRACE \S+(  SHIELD \S+)?(  DECOY \S+)?$'+'\nSelect ICE to inspect and target.'),
    ('HP 90/90  INT 100  TRACE 23.8  SHIELD 15  DECOY 2',
     r'^HP [\d.]+/[\d.]+  INT \S+  TRACE \S+(  SHIELD \S+)?(  DECOY \S+)?$'),
    ('Programs load only while idle. 12s remaining.', 'Programs load only while idle. 12s remaining.'),
]


def test_the_earlier_countdowns_blank_exactly_what_they_did_before():
    for text, blanked in BLANKED_BEFORE_246:
        assert _without_countdowns(text) == blanked, text
    assert _without_countdowns(['on / 42s remaining', 'Crew turn: Noor (12s).']) == [
        r'on / (?<= / )\d+s remaining$', r'^Crew turn: (?P<keep>.+) \(\d+s\)\.$[Noor]']


def test_enter_on_a_flipped_latch_survives_a_reset_second_in_flight(runners):  # noqa: F811
    """In process, two runners: B's Enter on A's flipped control arrives after
    the reset countdown has ticked. It flips; it is not refused as stale."""
    game, a, b, _ = runners
    first = enter(game, a, 'lat-civic-ledger')
    control = control_room(game, first)
    flip(game, a)
    game.commit_commerce()
    second = enter(game, b, 'lat-civic-ledger')
    second.room = first.room
    lattice = game.core.lattice
    assert lattice.interlock.control_view(first.core, control)['reset_in'], 'A flip leaves a reset running'
    intent(game, b, 'lat.cancel')
    b.send(Frame('key', {'k': 'e', 't': 0}))
    game.step(2)
    assert b.session.id in lattice.encounters.menus
    seen = b.session.view_revision  # What B's door shows and its Enter carries.
    row = 'control:'+control.id
    assert any(r['id'] == row for r in lattice.encounters.rows(second, lattice.encounters.menus[b.session.id]))
    game.step(TICK_HZ+2)  # The Enter is slow: the reset ticks a second meanwhile.
    assert b.session.view_revision != seen
    b.send(Frame('intent', {'name': 'menu_select', 'args': {'row': row, 'key': 'enter'},
                            'request_id': 't246-latch', 'expected_revision': seen}))
    game.step(2)
    assert b.session.id not in lattice.encounters.menus, 'The Enter was refused as stale'
    assert second.procedure and second.procedure.kind == 'flip'
