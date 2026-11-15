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


# --- Amendment 4 and the stale-follower ruling (2026-11-15) -----------------

def test_amendment_4_an_owed_anchor_plays_before_the_record_lines_and_its_followers_drop(game: HeadlessServer) -> None:
    """p5's order: a line was just spoken, so 6.2 is still owed when the
    record opens. 6.2 plays first, then 9.2; 6.3, 6.5 and 6.4 never play
    after the record (technical lead's ruling), and 6.2 is never between
    9.8 and 9.9 (creative lead)."""
    from tests.characters.test_s48c1b_lessons import approach, drone_of
    client = game.connect()
    session, clock = client.session, game.core.clock
    state = game.core.opening.states[session.id]
    game.step(2)
    approach(game, client)
    pid = session.player_id
    actor = game.core.world.actors[pid]
    # The fight with the speech gap kept running (p5: Okafor's 7.1/7.2 had
    # just been spoken), so 6.2 is queued at the first hit and still owed.
    for _ in range(1200):
        drone = drone_of(game, pid)
        if drone is None:
            break
        session.voice_tick = clock.now_tick()
        if "first_hit" in state["facts"]:
            actor.target = drone.id
            game.core.combat.fire(pid)
        game.step()
    assert "6.2" not in state["spoken"] and "6.2" in [q[0] for q in state["queue"]]
    heard = [line.get("script") for line in said(game, client)]
    place(game, client, HALL, DOOR)
    act(game, client, Frame("key", {"k": "e", "t": 0}))  # The record, within the speech gap.
    assert game.core.characters.wakes[session.id].stage == "record"
    assert state["queue"][0][0] == "6.2"
    for _ in range(120):
        game.step()
        heard += [line.get("script") for line in said(game, client)]
        if "9.2" in heard:
            break
    assert "6.2" in heard and heard.index("6.2") < heard.index("9.2")
    act(game, client, Frame("intent", {"name": "menu_select", "args": {}}))
    wake = game.core.characters.wakes[session.id]
    act(game, client, Frame("line", {"id": wake.prompt_id, "text": "Noor"}))
    game.step(1)
    game.commit_characters()
    for _ in range(600):
        game.step()
        heard += [line.get("script") for line in said(game, client)]
    after = heard[heard.index("9.2"):]
    assert not {"6.3", "6.4", "6.5"} & set(after)  # Stale after the record.
    assert after.index("9.8") < after.index("9.9") and "6.2" not in after[after.index("9.8"):]


def test_beat_six_6_2_and_6_5_speak_in_the_fight_and_6_3_6_4_drop_when_the_next_beat_begins(game: HeadlessServer) -> None:
    """Approved design (first-ten-minutes, Pacing and beat 6): 6.2 and 6.5
    are spoken during the fight, from the first hit; 6.3 and 6.4 are
    ordinary lines, dropped once the fight has ended and the next beat has
    begun. 6.5 passes the held 6.3; nothing else jumps the queue."""
    from tests.characters.test_s48c1b_lessons import armed_and_warned, drone_of
    client = game.connect()
    session = client.session
    state = game.core.opening.states[session.id]
    pid, drone, actor = armed_and_warned(game, client)  # The first hit: 6.2 is queued.
    for _ in range(200):  # The fight goes on; nobody fires back yet.
        if "6.5" in state["spoken"]:
            break
        game.step()
    assert drone_of(game, pid) is not None  # Still fighting.
    assert state["spoken"].index("6.2") < state["spoken"].index("6.5")
    assert not {"6.3", "6.4"} & set(state["spoken"])  # Held by the fight.
    for _ in range(1200):
        if drone_of(game, pid) is None:
            break
        actor.target = drone.id
        game.core.combat.fire(pid)
        game.step()
    for _ in range(300):
        game.step()
    beats = [b.id for b in game.core.opening.script.beats]
    assert state["beat"] == beats.index("the-terminal")
    assert not {"6.3", "6.4"} & set(state["spoken"])  # Dropped at drone_down.


# --- Each polled trigger fires only its own line or beat (bug fix, 2026-11-15)
# Before this fix a polled trigger fired every line whose trigger had the same
# name: entering beat 1's region fired 7.1/7.2 and jumped past beats 2-5.

def test_entering_beat_ones_region_fires_neither_7_1_nor_7_2_and_skips_no_beat(game: HeadlessServer) -> None:
    client = game.connect()
    game.step(2)
    state = game.core.opening.states[client.session.id]
    beats = [b.id for b in game.core.opening.script.beats]
    state["beat"] = beats.index("the-shape")
    place(game, client, HALL, (5, 9))  # Beat 1's `next` region (y 8-19), far from 7.1/7.2's.
    game.step(3)
    assert state["beat"] == beats.index("the-lease")
    assert not {"7.1", "7.2"} & set(state["fired"])


def test_7_1_and_7_2_fire_in_their_own_regions(game: HeadlessServer) -> None:
    client = game.connect()
    game.step(2)
    state = game.core.opening.states[client.session.id]
    beats = [b.id for b in game.core.opening.script.beats]
    state["beat"] = beats.index("the-terminal")
    place(game, client, HALL, (27, 14))
    game.step(2)
    assert "7.1" in state["fired"] and "7.2" not in state["fired"]
    place(game, client, HALL, (35, 15))
    game.step(2)
    assert "7.2" in state["fired"]


@pytest.mark.parametrize("name", ["steps", "seconds", "target_set", "zone_entered"])
def test_a_valueless_polled_trigger_fires_only_its_own_line(repo_root, name: str) -> None:
    """Two lines with a same-name trigger: one in the current beat, one later.
    Only the current one fires, and the beat does not jump."""
    from copy import deepcopy
    from dataclasses import replace
    from karst41.content.loader import load_content
    from karst41.content.schema import ScriptTrigger
    content = deepcopy(load_content(repo_root / "src/karst41/content"))
    script = content.scripts["wake-opening"]
    beats = list(script.beats)
    template = next(ln for b in beats for ln in b.lines if ln.id == "6.0")
    trigger = ScriptTrigger(name, count=1)
    here = replace(template, id="iso.here", when=(trigger,), after="", delay=0)
    later = replace(template, id="iso.later", when=(ScriptTrigger(name, count=1),), after="", delay=0)
    terminal = next(i for i, b in enumerate(beats) if b.id == "the-terminal")
    beats[0] = replace(beats[0], lines=(*beats[0].lines, here))
    beats[terminal] = replace(beats[terminal], lines=(*beats[terminal].lines, later))
    content.scripts["wake-opening"] = replace(script, beats=tuple(beats))
    game = HeadlessServer(content, character_state={})
    client = game.connect()
    game.step(1)
    opening = game.core.opening
    state = opening.states[client.session.id]
    actor = game.core.characters.sim.avatars[client.session.user_id].actor
    if name == "steps":
        place(game, client, HALL, (actor.x + 1, actor.y))
    elif name == "target_set":
        game.step(5)  # The hall's cast spawns.
        actor.target = next(a.id for a in game.core.world.actors.values()
                            if a.zone == HALL and a.kind != "player" and a.id != actor.id)
    elif name == "zone_entered":
        place(game, client, "vatside-clinics", (30, 18))
    for _ in range(12):
        game.step()
        if "iso.here" in state["fired"]:
            break
    assert "iso.here" in state["fired"]
    assert "iso.later" not in state["fired"] and state["beat"] == 0


def test_owed_lease_anchors_wait_under_the_record_and_follow_9_9(game: HeadlessServer) -> None:
    """First-ten-minutes, Pacing (worked example): 3.1 and 3.3, still owed
    at the record, do not speak under it; they follow 9.8 and 9.9. Only 6.2
    goes first (amendment 4). The kit waits for them (amendment 5)."""
    client = game.connect()
    session, clock = client.session, game.core.clock
    state = game.core.opening.states[session.id]
    game.step(2)
    beats = [b.id for b in game.core.opening.script.beats]
    state["beat"] = beats.index("the-shape")
    place(game, client, HALL, (5, 9))  # Beat 1's region: the lease beat begins.
    game.step(1)
    assert state["beat"] == beats.index("the-lease")
    session.voice_tick = clock.now_tick()  # A line was just spoken: 3.1/3.3 still owed.
    place(game, client, HALL, DOOR)
    act(game, client, Frame("key", {"k": "e", "t": 0}))
    assert game.core.characters.wakes[session.id].stage == "record"
    assert {"3.1", "3.3"} <= {q[0] for q in state["queue"]}
    heard = []
    for _ in range(120):
        game.step()
        heard += [line.get("script") for line in said(game, client)]
        if "9.2" in heard:
            break
    assert "9.2" in heard and not {"3.1", "3.3"} & set(heard)  # Not under the record.
    act(game, client, Frame("intent", {"name": "menu_select", "args": {}}))
    wake = game.core.characters.wakes[session.id]
    act(game, client, Frame("line", {"id": wake.prompt_id, "text": "Noor"}))
    game.step(1)
    game.commit_characters()
    place(game, client, HALL, DOOR)  # Where 10.1 and 10.2 are said.
    at = {}
    for _ in range(900):
        game.step()
        for line in said(game, client):
            heard.append(line.get("script"))
            at.setdefault(line.get("script"), clock.now_tick())
    order = ["9.2", "9.8", "9.9", "3.1", "3.3", "9.10", "9.10h", "9.11", "10.1"]
    assert [lid for lid in heard if lid in order] == order  # Amendment 5: one Vantongeren block, then the kit.
    assert at["9.10"] - at["3.3"] == 25  # 2.5 s after the last of her lines.
    assert (at["9.10h"] - at["9.10"], at["9.11"] - at["9.10"]) == (25, 50)  # The kit keeps its spacing.


# --- Resumed runner state (review of b53ade5, 6889042221) --------------------

def test_a_resumed_state_counts_seconds_from_this_attach(game: HeadlessServer) -> None:
    """Saved ticks belong to the process that wrote them: after a server
    restart, beat 0's `seconds: 12` must still fire 12 s after the attach."""
    client = game.connect()
    game.step(1)
    opening = game.core.opening
    state = opening.stop(client.session)
    state["beat"], state["beat_tick"] = 0, game.core.clock.now_tick() + 10**7  # An older, longer-running process.
    state["queue"] = [["2.1", 1, game.core.clock.now_tick() + 10**7]]  # An anchor with an old due tick.
    opening.start(client.session, state)
    assert state["beat_tick"] == game.core.clock.now_tick() and state["queue"][0][2] == 0
    game.step(12 * 10 + 2)
    assert state["beat"] >= 1


def test_a_resumed_state_naming_lines_or_beats_the_content_lacks_still_attaches(game: HeadlessServer) -> None:
    client = game.connect()
    game.step(1)
    opening = game.core.opening
    state = opening.stop(client.session)
    state["beat"], state["queue"] = 999, [["gone.1", 3, 0], ["9.8", 8, 0]]
    opening.start(client.session, state)  # Never raises on an old record.
    assert state["beat"] == len(opening.script.beats) - 1
    assert [q[0] for q in state["queue"]] == ["9.8"]
    game.step(5)


def test_a_disconnect_between_name_and_commit_still_gives_the_kit_lines_and_the_errand(game: HeadlessServer) -> None:
    """Review 6889245226: kit_issued had no session to reach when the caller
    left during "saving". The errand is set at the commit, and the next attach
    plays the kit lines."""
    client = game.connect()
    game.step(1)
    opening = game.core.opening
    state = opening.states[client.session.id]
    record = next(i for i, beat in enumerate(opening.script.beats) if beat.id == "the-record")
    state["fired"] += [ln.id for beat in opening.script.beats[:record] for ln in beat.lines]
    state["queue"] = []
    place(game, client, HALL, DOOR)
    act(game, client, Frame("key", {"k": "e", "t": 0}))
    game.step(10)
    act(game, client, Frame("intent", {"name": "menu_select", "args": {}}))
    wake = game.core.characters.wakes[client.session.id]
    act(game, client, Frame("line", {"id": wake.prompt_id, "text": "Noor"}))
    game.core.sessions.disconnect(client.session)  # Gone while the record saves.
    game.commit_characters()
    manager = game.core.characters
    player = next(p for p in manager.players.values() if p.name == "Noor")
    assert player.progress.get("errand") is True
    back = game.connect()
    assert back.session.player_id == player.id
    heard = []
    for _ in range(300):
        game.step()
        heard += [line.get("script") for line in said(game, back)]
    assert {"9.10", "9.11"} <= set(heard)  # The kit lines, on the next attach.


def test_the_speech_hold_keeps_real_combats_ten_seconds_after_a_drone_line(game: HeadlessServer) -> None:
    """Review 6889245226: a real hit, then a drone line, then the drone down:
    speech still waits for the real hit's ten seconds (amendment 3)."""
    from karst41.server.danger import holds
    from tests.characters.test_s48c1b_lessons import armed_and_warned, fight
    client = game.connect()
    pid, drone, actor = armed_and_warned(game, client)
    game.core.combat.log(actor, "Someone hits you for 5.", True)  # Real combat.
    game.step(1)
    fight(game, client)  # Drone lines after it; then the drone is down.
    session = client.session
    assert session.danger_source.startswith("training:")
    assert holds(session, game.core.clock.now_utc(), game.core.characters.training_drone_can_act)
