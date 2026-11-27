"""#301: a refused stale menu choice tells the caller, once, with a toast.

Session.fresh (S48e) refuses a menu_select whose menu changed its terms while
the Enter was in flight, and re-sends the whole current view. Before #301 the
player saw nothing else, so a refused Enter looked like a lost key. Now one
toast goes out with the re-sent view (presentation lead 7226785737):

    The menu changed, so nothing was chosen. Choose again.

role ui_text, 4 s, never the log, no row flash, and the guard unchanged. The
technical lead's condition (7226798416): only while the refused choice's menu
is still the menu on screen, the same kind and title changed in place. A
double-pressed Enter, refused because the first one was accepted and replaced
the menu, stays silent.
"""

from karst41.server.protocol import Frame
from karst41.server.sessions import CHOICE_HISTORY, MENU_HISTORY, STALE_CHOICE_TOAST, Session
from tests.lattice.test_encounters import enter
from tests.lattice.test_engine import intent, runners  # noqa: F401  (fixture)
from tests.lattice.test_interlock import control_room, flip

TOAST = {'text': STALE_CHOICE_TOAST, 'role': 'ui_text', 'seconds': 4}


def menu_view(rev: int, title: str, phase: str, kind: str = 'menu') -> Frame:
    return Frame('view', {'rev': rev, 'kind': kind, 'body': {
        'title': title, 'cursor': 0, 'detail': 'Select ICE to inspect and target.',
        'rows': [{'id': 'ice:warden', 'cells': ['Warden', '100', phase]}],
        'actions': [{'key': 'enter', 'label': 'select'}]}})


def select(revision: int, row: str = 'ice:warden') -> Frame:
    return Frame('intent', {'name': 'menu_select', 'args': {'row': row, 'key': 'enter'},
                            'request_id': f't301-{revision}', 'expected_revision': revision})


def session_with(*views: Frame) -> Session:
    session = Session('test:session:1', 'caller', {}, {})
    for tick, view in enumerate(views, 1):
        assert session.publish_view(view, None, tick)
    session.send_queue.clear()
    return session


def toasts(frames) -> list[dict]:
    return [frame.data for frame in frames if frame.type == 'toast']


def test_the_toast_fits_the_80_column_hint_row():
    assert len(STALE_CHOICE_TOAST) == 54  # Presentation lead: whole in the 78-cell hint row (#261).


def test_a_menu_that_changed_in_place_refuses_the_enter_and_says_so_once():
    session = session_with(menu_view(1, 'ROOM', 'warning'), menu_view(2, 'ROOM', 'attacking'))
    session.handling = enter_frame = select(1)
    assert not session.fresh(1)  # The guard is unchanged.
    assert session.needs_full_view
    assert toasts(session.send_queue) == [TOAST]
    assert not session.fresh(1)  # A second route checking the same Enter.
    assert toasts(session.send_queue) == [TOAST]
    session.handling = select(1)  # A new Enter, still stale, is told again.
    assert enter_frame is not session.handling
    assert not session.fresh(1)
    assert toasts(session.send_queue) == [TOAST, TOAST]


def test_a_refusal_after_an_accepted_choice_replaced_the_menu_is_silent():
    # The first Enter was accepted and opened another menu (or closed this
    # one); the doubled second Enter still carries revision 1.
    for after in (menu_view(2, 'BIND PROGRAM', 'warning'), menu_view(2, 'ROOM', 'warning', kind='rig'),
                  Frame('view', {'rev': 2, 'kind': 'zone', 'body': {'title': 'ROOM'}})):
        session = session_with(menu_view(1, 'ROOM', 'warning'), after)
        session.handling = select(1)
        assert not session.fresh(1)
        assert session.needs_full_view
        assert toasts(session.send_queue) == [], after.data


def test_a_doubled_enter_on_a_menu_that_stays_open_is_silent():
    # A purchase keeps its menu open and changes its totals in place: the
    # same kind and title. The doubled Enter, made on the view the accepted
    # one was made from, is refused without a word; a later Enter is told.
    session = session_with(menu_view(1, 'MARKET', 'ready'), menu_view(2, 'MARKET', 'ready'))
    session.handling = select(1)
    assert session.fresh(1)  # The first Enter: equivalent terms, accepted.
    assert session.chosen_from == 1
    assert session.publish_view(menu_view(3, 'MARKET', 'bought'), None, 3)
    session.handling = select(1)
    assert not session.fresh(1)
    assert toasts(session.send_queue) == []
    session.handling = select(2)  # Made on a view after that choice, and stale.
    assert not session.fresh(2)
    assert toasts(session.send_queue) == [TOAST]


def test_only_a_menu_select_is_told():
    session = session_with(menu_view(1, 'ROOM', 'warning'), menu_view(2, 'ROOM', 'attacking'))
    assert not session.fresh(1)  # Outside the tick loop's dispatch.
    for other in (Frame('intent', {'name': 'menu_cursor', 'args': {'delta': 1}, 'expected_revision': 1}),
                  Frame('intent', {'name': 'market_buy', 'args': {}, 'expected_revision': 1}),
                  Frame('key', {'k': 'enter', 't': 0})):
        session.handling = other
        assert not session.fresh(1)
    assert toasts(session.send_queue) == []


def test_a_slow_link_past_the_choice_history_is_still_told():
    # Beyond CHOICE_HISTORY no choice is let through, but which menu was on
    # screen is kept for MENU_HISTORY revisions (6.4 s), so the caller learns why.
    views = [menu_view(rev, 'ROOM', 'warning' if rev % 2 else 'attacking') for rev in range(1, CHOICE_HISTORY+3)]
    session = session_with(*views)
    assert 1 not in session.choices and len(session.choices) == CHOICE_HISTORY
    session.handling = select(1)
    assert not session.fresh(1)
    assert toasts(session.send_queue) == [TOAST]


def test_a_revision_too_old_to_compare_is_silent():
    views = [menu_view(rev, 'ROOM', 'warning' if rev % 2 else 'attacking') for rev in range(1, MENU_HISTORY+3)]
    session = session_with(*views)
    session.handling = select(1)
    assert not session.fresh(1)
    assert toasts(session.send_queue) == []
    assert len(session.menus) == MENU_HISTORY and len(session.choices) == CHOICE_HISTORY


def test_a_fresh_or_equivalent_choice_is_not_told():
    session = session_with(menu_view(1, 'ROOM', 'warning'), menu_view(2, 'ROOM', 'warning'))
    session.handling = select(1)
    assert session.fresh(1) and session.fresh(2)
    assert toasts(session.send_queue) == []


def test_a_latch_flipped_under_an_open_room_menu_toasts_the_stale_enter(runners):  # noqa: F811
    """In process: B has the room menu open when A flips the hall doors. B's
    Enter on the control, made on the old revision, means something else now:
    refused, with the toast, and the menu stays open on the new terms."""
    game, a, b, _ = runners
    first = enter(game, a, 'lat-civic-ledger')
    control = control_room(game, first)
    second = enter(game, b, 'lat-civic-ledger')
    second.room = first.room
    lattice = game.core.lattice
    intent(game, b, 'lat.cancel')
    b.send(Frame('key', {'k': 'e', 't': 0}))
    game.step(2)
    assert b.session.id in lattice.encounters.menus
    seen = b.session.view_revision
    flip(game, a)
    game.commit_commerce()
    game.step(2)
    assert lattice.interlock.control_view(first.core, control)['reset_in'], 'The doors flipped'
    game.frames_for(b)
    b.send(select(seen, 'control:'+control.id))
    game.step(2)
    frames = game.frames_for(b)
    assert toasts(frames) == [TOAST]
    assert b.session.id in lattice.encounters.menus, 'The refused Enter left the menu open'
    assert not second.procedure, 'Nothing was chosen'
    assert not any(frame.type == 'log' and STALE_CHOICE_TOAST in str(frame.data) for frame in frames)


def test_a_double_pressed_enter_on_a_room_menu_stays_silent(runners):  # noqa: F811
    """In process: B presses Enter twice on the hall doors. The first flips
    them and closes the menu; the second, carrying the same revision, says
    nothing."""
    game, _, b, _ = runners
    second = enter(game, b, 'lat-civic-ledger')
    control = control_room(game, second)
    lattice = game.core.lattice
    intent(game, b, 'lat.cancel')
    b.send(Frame('key', {'k': 'e', 't': 0}))
    game.step(2)
    seen = b.session.view_revision
    game.frames_for(b)
    b.send(select(seen, 'control:'+control.id))
    game.step(2)
    assert b.session.id not in lattice.encounters.menus
    assert second.procedure and second.procedure.kind == 'flip'
    b.send(select(seen, 'control:'+control.id))
    game.step(2)
    assert toasts(game.frames_for(b)) == []
    assert second.procedure and second.procedure.kind == 'flip'
