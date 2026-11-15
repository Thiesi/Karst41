"""#254 (S48c3a): the once-per-run crew notice waits for a new player's first street.

A character whose opening isn't complete hears "Crews end on a service
restart" when they first enter a non-pocket zone, not in the Wake Hall on
top of the kit. Returning players are unchanged, and it is once per run.
"""

import json

from karst41.server.protocol import Frame
from tests.character_helpers import act, create, place, state
from tests.harness import HeadlessServer

NOTICE = "Crews end on a service restart"
CLINIC_ROW = "vatside-clinics"


def heard(game: HeadlessServer, client) -> int:
    return sum(json.dumps(f.data, ensure_ascii=False).count(NOTICE) for f in game.frames_for(client))


def create_in_the_opening(game: HeadlessServer, client, name: str = "Noor") -> None:
    """Character creation with the opening still live (unlike `create`)."""
    manager = game.core.characters
    game.step()
    manager.take_card(client.session, "unmoored")
    assert manager.open_record(client.session, "door")
    game.step(4)
    act(game, client, Frame("intent", {"name": "menu_select", "args": {}}))
    act(game, client, Frame("line", {"id": manager.wakes[client.session.id].prompt_id, "text": name}))
    game.commit_characters()
    game.step()
    game.commit_characters()
    player = manager.players[client.session.player_id]
    assert player.name == name and not player.progress["opening"].get("done")


def test_a_new_character_hears_it_on_clinic_row_not_in_the_hall(game: HeadlessServer) -> None:
    client = game.connect()
    create_in_the_opening(game, client)
    game.step(40)
    assert heard(game, client) == 0
    place(game, client, CLINIC_ROW, (3, 2))
    game.step(2)
    assert heard(game, client) == 1
    place(game, client, "vatside-wake-hall", (20, 14))
    place(game, client, CLINIC_ROW, (3, 2))
    game.step(40)
    assert heard(game, client) == 0  # Once per run.


def test_a_redial_from_the_hall_still_waits_for_the_street(game: HeadlessServer) -> None:
    client = game.connect()
    create_in_the_opening(game, client)
    game.core.sessions.disconnect(client.session)
    game.step(2)
    back = game.connect()
    game.step(40)
    assert heard(game, back) == 0
    place(game, back, CLINIC_ROW, (3, 2))
    game.step(2)
    assert heard(game, back) == 1


def test_a_returning_character_hears_it_on_attach_once_per_run(game: HeadlessServer) -> None:
    client = game.connect()
    create(game, client)  # The direct path: the opening is finished.
    restart = HeadlessServer(game.core.loop.content, character_state=state(game))
    back = restart.connect()
    restart.step(2)
    assert heard(restart, back) == 1
    restart.core.sessions.disconnect(back.session)
    restart.step(2)
    again = restart.connect()
    restart.step(2)
    assert heard(restart, again) == 0
