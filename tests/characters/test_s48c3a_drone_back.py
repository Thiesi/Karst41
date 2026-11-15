"""S48c3a: movement wins over the drone lesson's B (#249 ruling, beat 6).

A key the caller's keymap binds to a move never skips the lesson. Literal B
skips and is consumed while a live drone is owed, and sprints as normal once
none is. Each case uses its own fresh game.
"""

import pytest

from karst41.server.protocol import Frame
from tests.character_helpers import act
from tests.characters.test_s48c1b_lessons import NEAR_DRONE, approach, drone_of, fight
from tests.harness import HeadlessServer

DOWN_LEFT = (NEAR_DRONE[0]-1, NEAR_DRONE[1]+1)


def press(game: HeadlessServer, keymap: str, key: str):
    client = game.connect()
    game.step(2)
    client.session.keymap_id = keymap
    approach(game, client)
    actor = game.core.characters.sim.avatars[client.session.user_id].actor
    assert actor.position == NEAR_DRONE and drone_of(game, client.session.player_id)
    act(game, client, Frame("key", {"k": key, "t": 0}))
    game.step(3)
    return client, actor


def lesson(game: HeadlessServer, client):
    return game.core.characters.wakes[client.session.id].lessons.get("drone")


def test_vi_b_walks_down_left_and_keeps_the_lesson(game: HeadlessServer) -> None:
    client, actor = press(game, "vi", "b")
    assert actor.position == DOWN_LEFT
    assert lesson(game, client) is None and drone_of(game, client.session.player_id)


@pytest.mark.parametrize(("keymap", "key"), [("vi", "B"), ("arrows", "B"), ("arrows", "b")])
def test_b_that_is_not_a_move_skips_and_is_consumed(game: HeadlessServer, keymap: str, key: str) -> None:
    client, actor = press(game, keymap, key)
    assert lesson(game, client) == "skipped" and drone_of(game, client.session.player_id) is None
    assert actor.position == NEAR_DRONE  # vi B is sprint_down_left: consumed, no sprint.


@pytest.mark.parametrize("ended", ["skipped", "down", "inert"])
def test_vi_capital_b_sprints_once_no_drone_is_owed(game: HeadlessServer, ended: str) -> None:
    client = game.connect()
    game.step(2)
    client.session.keymap_id = "vi"
    approach(game, client)
    pid = client.session.player_id
    actor = game.core.characters.sim.avatars[client.session.user_id].actor
    if ended == "skipped":
        act(game, client, Frame("key", {"k": "B", "t": 0}))
        assert lesson(game, client) == "skipped"
    elif ended == "down":
        fight(game, client)
        assert lesson(game, client) == "done"
    else:
        drone_of(game, pid).behaviour["inert"] = True  # As quiet_drone leaves it.
    game.step(3)
    start = actor.position
    act(game, client, Frame("key", {"k": "B", "t": 0}))
    game.step(3)
    assert actor.position == (start[0]-1, start[1]+1)
    assert lesson(game, client) == {"skipped": "skipped", "down": "done", "inert": None}[ended]
