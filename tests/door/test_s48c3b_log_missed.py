"""S48c3b item 3 (architecture 3, #260): the door's `log_missed` signal.

A spoken line that arrived on this connection is missed when it is cut as
the newest entry, or leaves the visible log, without ever having been drawn
whole. The door then sends `log_missed` once per connection; the opening
shows the read-back row once, in the hall only.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from karst41.content.loader import load_content
from karst41.door.doorinfo import DoorInfo
from karst41.door.model import Display
from karst41.server.protocol import Frame
from tests.character_helpers import act, create, place
from tests.harness import HeadlessServer

ONE_ROW = 'Stand up.'
THREE_ROWS = ' '.join(['Many names vanish into the roll and never come back out of it again.'] * 3)  # 3 rows at 80x24.


def said(text: str, second: int) -> dict:
    return {'kind': 'speech', 'channel': 'zone', 'speaker': 'Vantongeren', 'text': text,
            'at': f'2026-11-15T12:00:{second:02}+00:00'}


def door(info: DoorInfo, presentation: bool) -> Display:
    model = Display(replace(info, width=80, height=24, color_depth='256'), presentation=presentation)
    model.receive(Frame('welcome', {}), 0)
    # A text view: the log is drawn under every view, by the shared selection.
    model.receive(Frame('view', {'rev': 1, 'kind': 'text', 'body': {'title': 'The hall', 'lines': ['Quiet.']}}), 0)
    return model


def show(model: Display, now: float, *lines: dict, replay: bool = False) -> list[Frame]:
    if lines:
        model.receive(Frame('log', {'lines': list(lines), **({'replay': True} if replay else {})}), now)
    model.dirty, model.last_render = True, -1.0
    model.render(now)
    return model.take_outbox()


MISSED = [Frame('intent', {'name': 'log_missed', 'args': {}})]


@pytest.mark.parametrize('presentation', [True, False])
def test_256_two_lines_at_once_hide_the_older_before_it_shows_whole(info, presentation):
    # A three-row line and a one-row line arrive together in a three-row log:
    # the stop rule (#256) hides the older one before it is ever drawn.
    model = door(info, presentation)
    assert model.layout.log.height == 3
    assert show(model, 1, said(THREE_ROWS, 1), said(ONE_ROW, 2)) == MISSED


def system(text: str, second: int) -> dict:
    return {'kind': 'system', 'channel': 'system', 'text': text, 'at': f'2026-11-15T12:00:{second:02}+00:00'}


@pytest.mark.parametrize('presentation', [True, False])
def test_256_the_stop_rule_is_what_hides_the_spoken_line(info, presentation):
    # Spoken A (one row), then a three-row system line, then a one-row one.
    # The old skip rule would still have shown A under the gap; the stop rule
    # ends the fill at the three-row line, so A is never drawn: missed.
    model = door(info, presentation)
    assert show(model, 1, said(ONE_ROW, 1), system(THREE_ROWS, 2), system('Ready.', 3)) == MISSED
    # Control: with the three-row line short enough to fit, A shows whole.
    model = door(info, presentation)
    assert show(model, 1, said(ONE_ROW, 1), system('Short.', 2), system('Ready.', 3)) == []


@pytest.mark.parametrize('presentation', [True, False])
def test_a_cut_newest_line_fires_at_once(info, presentation):
    model = door(info, presentation)
    assert show(model, 1, said(' '.join([THREE_ROWS] * 2), 1)) == MISSED


@pytest.mark.parametrize('presentation', [True, False])
def test_ordinary_scrolling_of_a_line_seen_whole_does_not_fire(info, presentation):
    model = door(info, presentation)
    assert show(model, 1, said(THREE_ROWS, 1)) == []  # Drawn whole.
    for second in range(2, 6):
        assert show(model, second, said(ONE_ROW + f' {second}', second)) == []


@pytest.mark.parametrize('presentation', [True, False])
def test_a_replay_on_attach_never_counts(info, presentation):
    model = door(info, presentation)
    assert show(model, 1, said(THREE_ROWS, 1), said(ONE_ROW, 2), replay=True) == []


@pytest.mark.parametrize('presentation', [True, False])
def test_once_per_connection_then_again_after_a_new_welcome(info, presentation):
    model = door(info, presentation)
    assert show(model, 1, said(THREE_ROWS, 1), said(ONE_ROW, 2)) == MISSED
    assert show(model, 3, said(THREE_ROWS, 3), said(ONE_ROW, 4)) == []  # A second miss sends nothing.
    model.receive(Frame('welcome', {}), 5)
    assert show(model, 6, said(THREE_ROWS, 6), said(ONE_ROW, 7)) == MISSED


# --- the server -----------------------------------------------------------

@pytest.fixture
def game(repo_root: Path) -> HeadlessServer:
    return HeadlessServer(load_content(repo_root/'src/karst41/content'), character_state={})


def lesson(game: HeadlessServer, client) -> str:
    game.step()
    view = game.core.characters.sim.views[client.session.id].previous
    return ((view or {}).get('body', {}).get('lesson') or {}).get('text', '')


def test_in_the_hall_the_read_back_shows_once_and_never_on_clinic_row(game):
    client = game.connect()
    game.step(2)
    act(game, client, Frame('intent', {'name': 'log_missed', 'args': {}}))
    assert lesson(game, client) == 'Ctrl-P reads back what you missed.'
    game.step(8 * 10 + 2)
    assert lesson(game, client) != 'Ctrl-P reads back what you missed.'  # Eight seconds.
    act(game, client, Frame('intent', {'name': 'log_missed', 'args': {}}))  # Already seen: nothing.
    assert lesson(game, client) != 'Ctrl-P reads back what you missed.'
    assert not any('You send intent' in line.get('text', '') for line in client.session.log_ring)
    # The flag goes on with the character: Clinic Row's fallback never shows it.
    opening = game.core.opening.states[client.session.id]
    assert 'read-back' in opening['hints']
    create_keeping_the_opening(game, client)
    player = game.core.characters.players[client.session.player_id]
    place(game, client, 'vatside-clinics', (3, 2))
    for _ in range(400):
        assert lesson(game, client) != 'Ctrl-P reads back what you missed.'
        if 'shift-runs' in player.progress.get('hints_seen', []):
            break
    assert 'read-back' in player.progress.get('hints_seen', [])


def create_keeping_the_opening(game: HeadlessServer, client) -> None:
    from tests.characters.test_s48c3a_crew_notice import create_in_the_opening
    create_in_the_opening(game, client)


def test_outside_the_hall_the_intent_is_ignored(game):
    client = game.connect()
    game.step(2)
    place(game, client, 'vatside-clinics', (3, 2))
    act(game, client, Frame('intent', {'name': 'log_missed', 'args': {}}))
    assert 'read-back' not in game.core.opening.states[client.session.id]['hints']
    assert lesson(game, client) != 'Ctrl-P reads back what you missed.'


def test_with_no_live_opening_the_intent_is_ignored(game):
    client = game.connect()
    create(game, client)  # The direct path finishes the opening.
    act(game, client, Frame('intent', {'name': 'log_missed', 'args': {}}))
    assert lesson(game, client) != 'Ctrl-P reads back what you missed.'
    assert not any('You send intent' in line.get('text', '') for line in client.session.log_ring)


def test_the_server_honours_only_the_first_log_missed_per_connection(game):
    # The first arrives outside the hall and is ignored; the second and
    # third, sent from inside the hall on the same connection, are dropped.
    client = game.connect()
    game.step(2)
    place(game, client, 'vatside-clinics', (3, 2))
    act(game, client, Frame('intent', {'name': 'log_missed', 'args': {}}))
    place(game, client, 'vatside-wake-hall', (20, 14))
    for _ in range(2):
        act(game, client, Frame('intent', {'name': 'log_missed', 'args': {}}))
    assert 'read-back' not in game.core.opening.states[client.session.id]['hints']
    assert lesson(game, client) != 'Ctrl-P reads back what you missed.'
    # A new connection may send it again, and that one is honoured.
    game.core.sessions.disconnect(client.session)
    game.step(2)
    back = game.connect()
    game.step(2)
    act(game, back, Frame('intent', {'name': 'log_missed', 'args': {}}))
    assert lesson(game, back) == 'Ctrl-P reads back what you missed.'
