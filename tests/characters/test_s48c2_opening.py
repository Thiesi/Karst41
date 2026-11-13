"""S48c2: the opening's beats runner (#220 section 4; technical lead's contract)."""

from karst41.server.protocol import Frame
from tests.character_helpers import act, create, place
from tests.harness import HeadlessServer

HALL = "vatside-wake-hall"


def said(game: HeadlessServer, client) -> list[dict]:
    return [line for frame in game.frames_for(client) if frame.type == "log" for line in frame.data["lines"]]


def texts(lines: list[dict]) -> list[str]:
    return [line["text"] for line in lines]


def test_the_first_line_comes_at_once_and_the_next_four_seconds_later(game: HeadlessServer) -> None:
    client = game.connect()
    game.step(1)
    first = said(game, client)
    speech = [line for line in first if line["kind"] == "speech"]
    assert speech and speech[0]["text"] == "Vantongeren: Open your eyes slowly. The light's cheap and it stays on."
    assert speech[0]["speaker"] == "Vantongeren"
    game.step(39)
    assert not [line for line in said(game, client) if line["kind"] == "speech"]
    game.step(2)
    assert any(t.startswith("Vantongeren: I'm Vantongeren.") for t in texts(said(game, client)))


def test_a_second_wake_hears_nothing_of_the_firsts_opening(game: HeadlessServer) -> None:
    a = game.connect()
    b = game.connect({"user_id": 2, "handle": "Other", "node_name": "test"})
    game.step(1)
    said(game, b)
    place(game, a, HALL, (22, 14))   # a's drone wakes: 6.0 and 6.1 are a's alone.
    game.step(3)
    mine = texts(said(game, a))
    theirs = texts(said(game, b))
    assert "A drone turns in the corridor. It has seen you. Its shots can't kill." in mine
    assert not any("drone" in t.lower() for t in theirs)


def test_the_drone_wakes_with_the_scripts_lines_not_the_engines(game: HeadlessServer) -> None:
    client = game.connect()
    game.step(1)
    place(game, client, HALL, (22, 14))
    game.step(3)
    lines = texts(said(game, client))
    assert "A pistol on a cable by the door. Your hand takes it before you decide." in lines
    assert not any(t.startswith("Your training drone wakes.") for t in lines)
    state = game.core.opening.states[client.session.id]
    assert game.core.opening.script.beats[state["beat"]].id == "the-drone"


def test_with_no_live_script_the_engine_still_speaks_for_the_drone(game: HeadlessServer) -> None:
    """Technical lead, fix 1: a drone spawned for a finished character (#239) is not silent."""
    client = game.connect()
    create(game, client, name="Noor", card="none")
    player = game.core.characters.players[client.session.player_id]
    game.core.opening.finish(game.core.opening.states[client.session.id])
    player.progress.pop("wake_combat", None)
    said(game, client)
    place(game, client, HALL, (22, 14))
    game.step(3)
    assert any(t.startswith("Your training drone wakes.") for t in texts(said(game, client)))


def speech_after(game: HeadlessServer, client, seconds: int) -> list[str]:
    out = []
    for _ in range(seconds * 10):
        game.step()
        out.extend(t for t in texts(said(game, client)))
    return out


def test_the_record_beat_speaks_inside_its_menu_and_9_7_comes_before_the_name(game: HeadlessServer) -> None:
    client = game.connect()
    game.step(1)
    manager = game.core.characters
    place(game, client, HALL, (37, 16))
    act(game, client, Frame("key", {"k": "e", "t": 0}))       # The street door: the record, no terminal.
    heard = speech_after(game, client, 5)
    assert "Okafor: No record in hand? I keep a copy. Read me the template line." in heard
    act(game, client, Frame("intent", {"name": "menu_select", "args": {}}))   # Hardline, no hit taken.
    heard = speech_after(game, client, 10)
    assert heard.index("Okafor: Hardline. Thought so.") < heard.index("Okafor: And a name. The roll wants one.")
    assert not any("furniture" in t for t in heard)            # 9.3's long form needs the drone's hit.
    wake = manager.wakes[client.session.id]
    act(game, client, Frame("line", {"id": wake.prompt_id, "text": "Noor"}))
    game.commit_characters()
    game.step(1)
    lines = texts(said(game, client))
    assert "Issued: sidearm, 50 rounds, baton, two patches, two rations." in lines   # At once, not paced.
    assert "200 chits. A card, on top: Vesper, the Tin Halo, Sodium Row." in lines


def test_speech_waits_while_the_card_table_is_open(game: HeadlessServer) -> None:
    client = game.connect()
    game.step(1)
    said(game, client)
    place(game, client, HALL, (8, 9))
    act(game, client, Frame("key", {"k": "e", "t": 0}))
    assert game.core.characters.screens[client.session.id][0] == "cards"
    assert not [t for t in speech_after(game, client, 9) if t.startswith(("Vantongeren:", "Okafor:"))]
    act(game, client, Frame("key", {"k": "escape", "t": 0}))
    assert [t for t in speech_after(game, client, 5) if t.startswith("Vantongeren:")]


def test_nothing_from_the_hall_plays_outside_it(game: HeadlessServer) -> None:
    client = game.connect()
    game.step(1)
    state = game.core.opening.states[client.session.id]
    assert state["queue"]                                      # 1.2 and 1.3 are waiting.
    said(game, client)                                         # 1.1 was said in the hall.
    place(game, client, "vatside-clinics", (30, 18))
    heard = speech_after(game, client, 10)
    assert not [t for t in heard if t.startswith(("Vantongeren:", "Okafor:"))] and not state["queue"]


def test_6_2_is_always_said_even_when_the_drone_is_skipped(game: HeadlessServer) -> None:
    client = game.connect()
    game.step(1)
    place(game, client, HALL, (22, 14))
    game.step(3)
    act(game, client, Frame("key", {"k": "b", "t": 0}))
    heard = speech_after(game, client, 12)
    assert "You walk past it. It keeps trying." in heard
    assert "The cable reels the pistol back to the wall." in heard
    assert any(t.startswith("Vantongeren: If you die, you'll wake up here") for t in heard)
    assert heard.index("The cable reels the pistol back to the wall.") < next(
        i for i, t in enumerate(heard) if t.startswith("Vantongeren: If you die"))
