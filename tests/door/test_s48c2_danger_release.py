"""Runner contract amendment 3 in the client's log (terminal UI 2.5, P12 rule 5).

A danger or combat line stops being protected once its source can no longer
act on the caller. The server says so with `me.danger_release` (the stamp up
to which the training drone's lines are released); real combat sends none
and keeps its ten seconds.
"""

from dataclasses import replace
from pathlib import Path

from karst41.content.loader import load_content
from karst41.door.doorinfo import DoorInfo
from karst41.door.model import Display
from karst41.server.clock import TICK_HZ
from tests.fake_terminal import FakeTerminal
from tests.harness import HeadlessServer


def screen(game: HeadlessServer, client, model: Display) -> list[str]:
    clock = game.core.clock
    for frame in game.frames_for(client):
        model.receive(frame, clock.now_tick() / TICK_HZ)
    model.toast = None
    terminal = FakeTerminal(80, 24)
    terminal.write(model.render(clock.now_tick() / TICK_HZ)[0])
    rows = terminal.screen_text().splitlines()
    return rows[model.layout.log.y:model.layout.log.bottom]


def test_after_drone_down_the_newest_lines_take_the_rows_at_80x24(info: DoorInfo, repo_root: Path) -> None:
    from tests.characters.test_s48c1b_lessons import armed_and_warned, fight
    game = HeadlessServer(load_content(repo_root/"src/karst41/content"), character_state={})
    client = game.connect(width=80, height=24)
    model = Display(replace(info, width=80, height=24, color_depth="256"), presentation=True)
    armed_and_warned(game, client)
    fight(game, client)  # The drone is down: 6.6 and 6.8 are said at once.
    state = game.core.opening.states[client.session.id]
    for _ in range(60):  # Within the old ten seconds, newer lines arrive (6.2, 7.1...).
        game.step()
        if len(state["spoken"]) >= 7:
            break
    assert game.core.characters.sim.views[client.session.id].previous["body"]["me"].get("danger_release")
    rows = screen(game, client, model)
    shown = "\n".join(rows)
    assert "The cable reels the pistol back" in shown
    assert not any(word in shown for word in ("Hit: Training drone", "Miss: Training drone", "hits you for")), shown


def test_real_combat_keeps_its_ten_seconds(info: DoorInfo, repo_root: Path) -> None:
    from tests.character_helpers import create
    game = HeadlessServer(load_content(repo_root/"src/karst41/content"), character_state={})
    client = game.connect(width=80, height=24)
    model = Display(replace(info, width=80, height=24, color_depth="256"), presentation=True)
    create(game, client, name="Noor", card="none")
    actor = game.core.world.actors[client.session.player_id]
    game.core.combat.log(actor, "Someone hits you for 5.", True)  # Untagged: real combat.
    for text in ("One.", "Two.", "Three.", "Four."):
        client.session.emit(text, kind="system", channel="system", role="chan_sys",
                            at=game.core.clock.now_utc().isoformat())
    game.step(5)
    assert "danger_release" not in game.core.characters.sim.views[client.session.id].previous["body"]["me"]
    assert "Someone hits you for 5." in "\n".join(screen(game, client, model))  # Still protected.


def test_the_release_only_moves_forward_and_a_newer_line_stays_protected(info: DoorInfo, repo_root: Path) -> None:
    """#253 (terminal UI 2.5): the stamp is on the server clock of `at`, never
    goes back, and a line newer than it keeps its full protection."""
    from datetime import timedelta
    from tests.characters.test_s48c1b_lessons import armed_and_warned, fight
    game = HeadlessServer(load_content(repo_root/"src/karst41/content"), character_state={})
    client = game.connect(width=80, height=24)
    model = Display(replace(info, width=80, height=24, color_depth="256"), presentation=True)
    session, views = client.session, game.core.characters.sim.views
    armed_and_warned(game, client)
    fight(game, client)
    game.step(2)
    first = views[session.id].previous["body"]["me"]["danger_release"]
    # An older drone stamp never replaces it, and it stays after the ten seconds.
    from datetime import datetime
    session.danger_at = (datetime.fromisoformat(first) - timedelta(seconds=3)).isoformat()
    game.step(12 * TICK_HZ)
    assert views[session.id].previous["body"]["me"]["danger_release"] == first
    # A new fight right after: its line is newer than the stamp, so it is protected.
    actor = game.core.characters.sim.avatars[session.user_id].actor
    game.core.combat.log(actor, "Someone hits you for 5.", True)
    for text in ("One.", "Two.", "Three.", "Four."):
        session.emit(text, kind="system", channel="system", role="chan_sys", at=game.core.clock.now_utc().isoformat())
    game.step(2)
    assert views[session.id].previous["body"]["me"]["danger_release"] == first
    assert "Someone hits you for 5." in "\n".join(screen(game, client, model))


def test_under_the_record_the_released_drone_lines_stay_unprotected_at_80x24(info: DoorInfo, repo_root: Path) -> None:
    """The presentation lead's 80x24 record frame: the Wake screen carries no
    `me`, so the client keeps the newest stamp it has seen."""
    from karst41.server.protocol import Frame
    from tests.character_helpers import act, place
    from tests.characters.test_s48c1b_lessons import armed_and_warned, fight
    game = HeadlessServer(load_content(repo_root/"src/karst41/content"), character_state={})
    client = game.connect(width=80, height=24)
    model = Display(replace(info, width=80, height=24, color_depth="256"), presentation=True)
    armed_and_warned(game, client)
    fight(game, client)
    game.step(2)
    screen(game, client, model)  # The zone view with the stamp.
    place(game, client, "vatside-wake-hall", (37, 16))
    act(game, client, Frame("key", {"k": "e", "t": 0}))  # The record, within the old ten seconds.
    state = game.core.opening.states[client.session.id]
    for _ in range(80):
        game.step()
        if "9.2" in state["spoken"]:
            break
    game.step(2)
    shown = "\n".join(screen(game, client, model))
    assert model.kind == "wake"
    assert "No record in hand" in shown
    assert not any(word in shown for word in ("Hit: Training drone", "Miss: Training drone", "hits you for")), shown
