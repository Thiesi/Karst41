"""S48c2: the opening's beats runner (#220 section 4; technical lead's contract)."""

import pytest

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
    """Technical lead, fix 1: a drone with no live script is not silent.

    Since S48c1b only an unread body gets a drone, so the case is an unread
    body whose runner is gone (a #239 replay or a direct spawn)."""
    client = game.connect()
    game.step(2)
    pid = client.session.player_id
    assert game.core.characters.unread(pid)
    game.core.opening.stop(client.session)
    assert not game.core.opening.live(pid)
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
    kit = "Issued: sidearm, 50 rounds, baton, two patches, two rations."
    vest = "Issued: a Kestrel Issue Vest. It's on you already."    # The item's own name (creative lead).
    card = "200 chits. A card, on top: Vesper, the Tin Halo, Sodium Row."
    assert kit not in lines and vest not in lines and card not in lines  # After 9.8 and 9.9 (creative lead).


DOOR, BY_OKAFOR = (37, 16), (34, 18)   # Inside and outside 10.1/10.2's region.


def take_kit(game: HeadlessServer, client, archetype: int, ticks: int = 200, leave_at: int | None = None,
             at: tuple[int, int] = DOOR, walk_to_door_at: int | None = None) -> list[tuple[int, dict]]:
    """Read the record as `archetype` (menu index) at `at`, name the body
    and return every log line after the name as (clock ticks since then, line).
    Earlier beats count as played, as for a caller who walked through them;
    kit_issued comes the tick after the name."""
    game.step(1)
    opening = game.core.opening
    state = opening.states[client.session.id]
    record = next(i for i, beat in enumerate(opening.script.beats) if beat.id == "the-record")
    state["fired"] += [ln.id for beat in opening.script.beats[:record] for ln in beat.lines]
    state["queue"] = []
    place(game, client, HALL, at)
    act(game, client, Frame("key", {"k": "e", "t": 0}))
    game.step(10)
    for _ in range(archetype):
        act(game, client, Frame("intent", {"name": "menu_cursor", "args": {"delta": 1}}))
    act(game, client, Frame("intent", {"name": "menu_select", "args": {}}))
    # The name is typed once the record's own lines are said and the
    # four-second speech gap is over, so 9.8 can speak the tick the kit lands.
    for _ in range(600):
        voice = client.session.voice_tick
        if not state["queue"] and (voice is None or game.core.clock.now_tick() - voice >= 40):
            break
        game.step()
    said(game, client)
    wake = game.core.characters.wakes[client.session.id]
    act(game, client, Frame("line", {"id": wake.prompt_id, "text": "Noor"}))
    clock = game.core.clock
    base = clock.now_tick()
    out = [(0, line) for line in said(game, client)]
    game.step(1)
    game.commit_characters()
    while clock.now_tick() - base < ticks:
        now = clock.now_tick() - base
        if now == leave_at:
            place(game, client, "vatside-clinics", (30, 18))
        elif now == walk_to_door_at:
            place(game, client, HALL, DOOR)
        else:
            game.step()
        out.extend((clock.now_tick() - base, line) for line in said(game, client))
    return out


def script_lines(heard: list[tuple[int, dict]]) -> list[tuple[int, str]]:
    return [(tick, line["script"]) for tick, line in heard
            if line.get("script", "").startswith(("9.8", "9.9", "9.1", "10."))]


def kit_lines(heard: list[tuple[int, dict]]) -> list[tuple[int, str]]:
    return [(tick, lid) for tick, lid in script_lines(heard) if lid.startswith("9.1")]


def test_at_the_street_door_beat_nine_closes_in_the_creative_leads_order(game: HeadlessServer) -> None:
    """Creative lead's beat 9: 9.8 at 0 s, 9.9 at 4 s, 9.10 at 6.5 s, the
    template line at 9 s, 9.11 at 11.5 s; then the door lines (fix 1,
    second amendment: 10.1 2.5 s after 9.11, 10.2 by speech pacing)."""
    client = game.connect()
    heard = script_lines(take_kit(game, client, 0))
    assert [lid for _, lid in heard] == ["9.8", "9.9", "9.10", "9.10h", "9.11", "10.1", "10.2"]
    start = heard[0][0]
    assert [tick - start for tick, _ in heard] == [0, 40, 65, 90, 115, 140, 180]


@pytest.mark.parametrize(("index", "own"), [(0, "9.10h"), (2, "9.10c"), (3, "9.10o")])
def test_only_the_callers_own_template_line_follows_the_kit(game: HeadlessServer, index: int, own: str) -> None:
    client = game.connect()
    kit = [lid for _, lid in kit_lines(take_kit(game, client, index))]
    assert kit == ["9.10", own, "9.11"]


def test_a_ghost_hears_the_kit_then_the_card_with_no_template_line(game: HeadlessServer) -> None:
    client = game.connect()
    kit = kit_lines(take_kit(game, client, 1))
    assert [lid for _, lid in kit] == ["9.10", "9.11"]
    assert kit[1][0] - kit[0][0] == 50


def test_read_at_okafor_the_door_lines_wait_for_the_door(game: HeadlessServer) -> None:
    """Fix 1, second amendment: 10.1 armed after 9.11 was spoken is queued at once."""
    client = game.connect()
    heard = script_lines(take_kit(game, client, 0, ticks=300, at=BY_OKAFOR, walk_to_door_at=220))
    early = [lid for tick, lid in heard if tick < 220]
    assert early == ["9.8", "9.9", "9.10", "9.10h", "9.11"]          # No door line away from the door.
    late = [(tick, lid) for tick, lid in heard if tick >= 220]
    assert [lid for _, lid in late] == ["10.1", "10.2"]
    assert late[0][0] - 220 <= 2 and late[1][0] - late[0][0] == 40


def test_leaving_the_hall_at_seven_seconds_keeps_what_was_said_and_drops_the_rest(game: HeadlessServer) -> None:
    """Technical lead: no exemption from the hall drop (a playtest watch item).
    At 7 s, 9.8, 9.9 and 9.10 were said; the template line, 9.11 and the
    door lines are dropped. The kit itself was issued either way."""
    client = game.connect()
    heard = take_kit(game, client, 0, leave_at=72)
    lines = script_lines(heard)
    assert lines[0] == (2, "9.8")                                    # 9.8 as the kit lands; leave 7 s later.
    assert [lid for _, lid in lines] == ["9.8", "9.9", "9.10"]
    assert not game.core.opening.states[client.session.id]["queue"]
    assert game.core.characters.players[client.session.player_id].progress.get("errand")


def test_the_paced_kit_lines_leave_speech_pacing_alone(game: HeadlessServer) -> None:
    """Fix 1 as amended: a delayed event line is never gated like speech and
    never sets voice_tick; speech keeps its own four seconds."""
    client = game.connect()
    heard = kit_lines(take_kit(game, client, 0, ticks=80))
    assert [lid for _, lid in heard] == ["9.10"]
    session, opening = client.session, game.core.opening
    state = opening.states[session.id]
    assert {q[0] for q in state["queue"]} >= {"9.10h", "9.11"}
    session.voice_tick = game.core.clock.now_tick()      # A line was just spoken.
    speech = opening.lines["10.2"][1]
    played = []
    for _ in range(50):
        before = session.voice_tick
        game.step()
        kit = [line["script"] for line in said(game, client) if line.get("script") in ("9.10h", "9.11")]
        if kit:
            played += kit
            assert session.voice_tick == before                       # A kit line never sets voice_tick,
            if kit == ["9.10h"]:                                      # and plays while speech must wait.
                assert opening.quiet(session, speech, game.core.clock.now_tick())
    assert played == ["9.10h", "9.11"]


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


def test_beat_one_moves_on_after_twelve_seconds_even_from_tick_zero(game: HeadlessServer) -> None:
    client = game.connect()           # Woken at tick 0: a beat's timer starts at 0.
    state = game.core.opening.states[client.session.id]
    assert state["beat_tick"] == 0 and state["beat"] == 0
    game.step(121)
    assert state["beat"] == 1          # The shape: 12 s with no three steps.


def test_beat_eights_hints_show_in_the_lattice_view(game: HeadlessServer) -> None:
    client = game.connect()
    game.step(1)
    place(game, client, HALL, (34, 15))
    act(game, client, Frame("key", {"k": "j", "t": 0}))
    game.step(4)
    view = game.core.characters.sim.external_views[client.session.id]
    assert view[0] == "lattice" and view[1]["lesson"] == {"text": "Tab picks the Tripwire.  B leaves"}


def test_an_unread_body_talks_to_vantongeren_and_hears_it_again(game: HeadlessServer) -> None:
    client = game.connect()
    game.step(1)
    manager = game.core.characters
    place(game, client, HALL, (14, 7))
    act(game, client, Frame("key", {"k": "e", "t": 0}))
    view = manager.sim.external_views[client.session.id]
    assert view[0] == "menu" and view[1]["kind"] == "talker"
    assert view[1]["detail"].startswith("Open your eyes slowly.")
    assert [a["label"] for a in view[1]["actions"]] == ["Hear it again"]   # Nothing that writes.
    assert manager.wakes[client.session.id].met == ["vantongeren"]
    assert not [e for e in manager.outbox if e["action"] in ("save", "create")]
    assert not game.core.commerce.outbox
    act(game, client, Frame("key", {"k": "h", "t": 0}))
    page = manager.sim.external_views[client.session.id]
    assert page[0] == "text" and page[1]["title"].endswith("1/3 (1/1)") and page[1]["flow"]
    assert page[1]["lines"][0].startswith("Open your eyes slowly.")
    act(game, client, Frame("key", {"k": "escape", "t": 0}))
    assert manager.sim.external_views[client.session.id][1]["kind"] == "talker"


def test_an_unread_body_is_present_to_talk_but_never_ready_to_write(game: HeadlessServer) -> None:
    """S48c2 split Commerce.ready into present (talk) and ready (write). The
    merge with main once resurrected the old unread refusal inside present."""
    client = game.connect()
    game.step(2)
    commerce, session = game.core.commerce, client.session
    assert game.core.characters.unread(session.player_id)
    assert commerce.present(session) and not commerce.ready(session)
    create(game, client, name="Noor", card="none")
    assert commerce.present(client.session) and commerce.ready(client.session)


# --- Runner contract amendment 3 (owner, 2026-11-15) -------------------------
# A danger or combat line stops holding speech once its source can no longer
# act on the caller: the training drone's lines hold during the lesson and
# release at down, skipped or inert. Real combat holds the full ten seconds.

def hold(game: HeadlessServer, client) -> bool:
    from karst41.server.danger import holds
    return holds(client.session, game.core.clock.now_utc(), game.core.characters.training_drone_can_act)


def test_amendment_3_the_drone_fight_holds_speech_until_the_drone_is_down(game: HeadlessServer) -> None:
    from tests.characters.test_s48c1b_lessons import armed_and_warned, fight
    client = game.connect()
    pid, drone, actor = armed_and_warned(game, client)  # The drone has hit.
    assert client.session.danger_source == f"training:{pid}"
    assert hold(game, client)  # Reading combat is part of the lesson.
    fight(game, client)
    assert client.session.danger_at is not None and hold(game, client) is False  # Down: released at once.


def test_amendment_3_the_hold_releases_when_the_drone_is_skipped(game: HeadlessServer) -> None:
    from tests.characters.test_s48c1b_lessons import armed_and_warned, drone_of
    client = game.connect()
    pid, drone, actor = armed_and_warned(game, client)
    assert hold(game, client)
    act(game, client, Frame("key", {"k": "b", "t": 0}))
    assert drone_of(game, pid) is None and hold(game, client) is False


def test_amendment_3_the_hold_releases_under_the_record_and_for_an_inert_drone(game: HeadlessServer) -> None:
    from tests.characters.test_s48c1b_lessons import armed_and_warned
    client = game.connect()
    manager = game.core.characters
    pid, drone, actor = armed_and_warned(game, client)
    assert manager.open_record(client.session, "door")
    assert hold(game, client) is False  # The drone holds fire under the record (S48c1b).
    act(game, client, Frame("intent", {"name": "menu_close", "args": {}}))
    assert hold(game, client)  # Esc: the fight resumes, so does the hold.
    create(game, client, name="Noor", card="none")  # A character's drone is inert.
    assert drone.behaviour["inert"] and hold(game, client) is False


def test_amendment_3_real_combat_still_holds_ten_seconds(game: HeadlessServer) -> None:
    from karst41.server.clock import TICK_HZ
    client = game.connect()
    create(game, client, name="Noor", card="none")
    actor = game.core.world.actors[client.session.player_id]
    game.core.combat.log(actor, "Someone hits you for 5.", True)  # Untagged: any other source.
    game.frames_for(client)
    assert client.session.danger_source is None and hold(game, client)
    game.step(10 * TICK_HZ - 1)
    assert hold(game, client)
    game.step(1)
    assert hold(game, client) is False


def test_amendment_3_p5s_pace_now_hears_the_record_lines(game: HeadlessServer) -> None:
    """The engineer's p5 diagnosis: the record opened about 5 s after the drone's
    last hit, so the hold kept 9.2 back until kit_issued dropped it."""
    from datetime import datetime
    from tests.characters.test_s48c1b_lessons import armed_and_warned, fight
    client = game.connect()
    pid, drone, actor = armed_and_warned(game, client)
    fight(game, client)
    session, clock = client.session, game.core.clock
    hit = datetime.fromisoformat(session.danger_at)
    said(game, client)
    place(game, client, HALL, DOOR)
    act(game, client, Frame("key", {"k": "e", "t": 0}))
    assert game.core.characters.wakes[session.id].stage == "record"
    assert (clock.now_utc() - hit).total_seconds() < 10  # Inside the old hold.
    heard = []
    for _ in range(60):  # At most a speech gap.
        game.step()
        heard += [line.get("script") for line in said(game, client)]
        if "9.2" in heard:
            break
    assert "9.2" in heard
