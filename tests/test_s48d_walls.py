"""S48d: a voice in the walls (S48 part 5; technical lead's rulings, 2026-11-19)."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from karst41.content import schema as s
from karst41.content.formats import read_record
from karst41.content.loader import load_content
from karst41.content.validate import validate
from karst41.server import walls as w
from karst41.server.clock import TICK_HZ
from karst41.server.npcs.templates import make_actor
from karst41.server.protocol import Frame
from karst41.server.script import fresh
from karst41.server.sessions import QUEUE_LIMIT
from tests.character_helpers import act, create, place
from tests.harness import HeadlessServer

ROW, HALL, PLAZA = 'sodium-row', 'vatside-wake-hall', 'core-plaza'


@pytest.fixture(scope='module')
def shipped():
    return load_content(Path(__file__).parents[1]/'src/karst41/content')


def quiet(content, *ids, **voice):
    """No spawners, so nothing barks or threatens unless a test places it.
    With ids, only those shipped lines, in file order; `voice` overrides fields."""
    content = deepcopy(content)
    content.zones = {k: replace(z, meta=replace(z.meta, spawners=())) for k, z in content.zones.items()}
    lines = tuple(ln for ln in content.walls.lines if not ids or ln.id in ids)
    content.walls = replace(content.walls, lines=lines, **voice)
    return content


def line(lid, name, text, zones=(ROW,), answer='', count=0, rects=()):
    return s.WallLine(id=lid, trigger=s.WallTrigger(name=name, count=count, rects=rects), text=text,
                      zones=zones, answer=answer)


def caller(game, uid, zone=ROW, tile=(25, 6)):
    client = game.connect({'user_id': uid, 'handle': f'Wall{uid}', 'node_name': 'S48d'})
    create(game, client, name=f'Wall{uid}', card='none')  # The direct path: no live opening.
    place(game, client, zone, tile)
    game.frames_for(client)
    return client


def heard(game, client):
    """The wall voice's entries this caller received: log_ambient lines from the file."""
    texts = {ln.text for ln in game.core.loop.content.walls.lines} | {
        ln.answer for ln in game.core.loop.content.walls.lines if ln.answer}
    out = []
    for frame in game.frames_for(client):
        if frame.type == 'log' and not frame.data.get('replay'):  # A reconnect's replay is not a new line.
            out += [ln for ln in frame.data['lines'] if ln['kind'] == 'ambient'
                    and (ln['text'] in texts or 'say it right' in ln['text'])]
    return out


def seconds(game, client, n):
    got = []
    for _ in range(n):
        game.step(TICK_HZ)
        got += heard(game, client)
    return got


def move(game, client, direction):
    act(game, client, Frame('intent', {'name': 'move', 'args': {'dir': direction}}))


def walls(game):
    return game.core.walls


def body(game, client):
    return game.core.world.actors[client.session.player_id]


def player(game, client):
    return game.core.characters.players[client.session.player_id]


# --- content -----------------------------------------------------------------------

def test_the_shipped_lines_validate_and_the_layer_is_on(shipped):
    assert shipped.walls is not None and shipped.walls.enabled and shipped.walls.per_evening == 3
    assert len(shipped.walls.lines) == 25
    assert not [p for p in validate(shipped) if 'walls' in p.path or 'line w' in p.message]
    order = [ln.id for ln in shipped.walls.lines]
    assert order.index('w10') < order.index('w11') and order.index('w19') < order.index('w06')


def test_the_reader_rejects_a_speaker_a_name_or_a_trigger_outside_the_set():
    for extra in ('"speaker": "tenant"', '"name": "The Tenant"'):
        record, problems = read_record('{"schema": 1, "lines": [{"id": "x", "trigger": {"name": "idle", "count": 5},'
                                       f' "text": "Hi.", {extra}}}]}}', 'walls.json', s.WallVoice)
        assert record is None and any('unknown field' in p.message for p in problems)
    record, problems = read_record('{"schema": 1, "lines": [{"id": "x", "trigger": {"name": "first_death"},'
                                   ' "text": "Hi."}]}', 'walls.json', s.WallVoice)
    assert record is None and any('is not one of' in p.message for p in problems)


@pytest.mark.parametrize('bad, message', [
    (line('a', 'region', 'Hi.', rects=((99, 0, 3, 1),)), 'outside sodium-row'),
    (line('a', 'region', 'Hi.', rects=((0, 0, 2, 2),)), "isn't passable"),
    (line('a', 'region', 'Hi.', zones=(), rects=((5, 5, 1, 1),)), 'one zone'),
    (line('a', 'idle', 'Hi.'), 'positive count'),
    (line('a', 'idle', 'Hi {item:x}.', count=5), 'token other than'),
    (line('a', 'zone_entered', 'Hi.', zones=()), 'names its zone'),
    # Two rows as written, three once a 24-cell name stands in for <name>.
    (line('a', 'idle', '<name>, ' + 'very long words that go on ' * 5, count=5), 'with a 24-cell name'),
    (line('a', 'idle', 'Short.', answer='An answer that runs on ' * 7, count=5), "doesn't fit whole"),
])
def test_the_validator_rejects(shipped, bad, message):
    content = deepcopy(shipped)
    content.walls = replace(content.walls, lines=(bad,))
    assert any(message in p.message for p in validate(content)), [p.message for p in validate(content)]


def test_walls_json_is_not_a_mechanics_fingerprint_input(shipped):
    from tests.presentation.mechanics import projection
    off = deepcopy(shipped)
    off.walls = None
    assert projection(off) == projection(shipped)


# --- triggers, each reaching the caller ---------------------------------------------

def test_bump_three_into_the_hall_west_wall_says_w02_to_that_caller_alone(shipped):
    game = HeadlessServer(quiet(shipped, 'w02'), character_state={})
    me, beside = caller(game, 1, HALL, (1, 8)), caller(game, 2, HALL, (2, 8))
    game.step(TICK_HZ)
    heard(game, me), heard(game, beside)
    for _ in range(2):
        move(game, me, 'left')
    assert heard(game, me) == []
    move(game, me, 'left')
    got = heard(game, me)
    assert [ln['text'] for ln in got] == ["The line goes to the door. I didn't paint it. I watched."]
    assert got[0]['role'] == 'log_ambient' and got[0]['channel'] == 'zone' and 'speaker' not in got[0]
    assert heard(game, beside) == []  # Caller-only, even with someone standing beside.
    assert any(entry.get('text') == got[0]['text'] for entry in me.session.log_ring)  # It replays.


def test_a_bump_into_an_actor_counts_nothing_and_any_other_input_resets(shipped):
    game = HeadlessServer(quiet(shipped, 'w15'), character_state={})
    me, other = caller(game, 1, ROW, (10, 20)), caller(game, 2, ROW, (11, 20))
    game.step(TICK_HZ)
    for _ in range(4):
        move(game, me, 'right')  # Into the other caller.
        game.step(5)
    assert walls(game).ears[me.session.id].bumps == 0 and heard(game, me) == []
    place(game, me, ROW, (1, 20))
    for _ in range(2):
        move(game, me, 'left')  # Into the west wall.
    act(game, me, Frame('intent', {'name': 'resync', 'args': {}}))  # Any other input.
    move(game, me, 'left')
    assert walls(game).ears[me.session.id].bumps == 1 and heard(game, me) == []
    move(game, me, 'left'), move(game, me, 'left')
    assert [ln['text'] for ln in heard(game, me)] == ["That one doesn't open. Try the ones with music."]


def test_idle_thirty_on_the_row(shipped):
    game = HeadlessServer(quiet(shipped, 'w16'), character_state={})
    me = caller(game, 1)
    assert seconds(game, me, 28) == []
    got = seconds(game, me, 3)
    assert [ln['text'] for ln in got] == ["You've stopped. Everyone else here is pretending not to."]


def test_region_outside_the_tin_halo_says_w18_and_its_answer_whole_and_adjacent(shipped):
    game = HeadlessServer(quiet(shipped, 'w18'), character_state={})
    me = caller(game, 1, ROW, (90, 26))
    got = seconds(game, me, 2)
    assert [ln['text'] for ln in got] == ["That sign never goes out. People think it's luck.",
                                          "Row grid: circuit 14 draws no current. Ticket closed, no fault."]
    assert {ln['role'] for ln in got} == {'log_ambient'} and got[0]['at'] == got[1]['at']


def test_name_is_the_callers_own_character_name(shipped):
    game = HeadlessServer(quiet(shipped, 'w19'), character_state={})
    me = caller(game, 1, HALL, (11, 4))
    assert [ln['text'] for ln in seconds(game, me, 2)] == ["Wall1. I'll try to say it right."]


def test_zone_entered_fires_after_a_real_arrival_on_the_row(shipped):
    game = HeadlessServer(quiet(shipped, 'w14'), character_state={})
    me = caller(game, 1, PLAZA, (62, 36))
    move(game, me, 'down')  # Onto the street exit to the Row.
    got = []
    for _ in range(40):
        game.step(TICK_HZ)
        got += heard(game, me)
        if body(game, me).zone == ROW and got:
            break
    assert body(game, me).zone == ROW
    assert [ln['text'] for ln in got] == ["There you are. It's loud here. I'll keep it short."]


def test_zone_entered_never_fires_past_its_window(shipped):
    game = HeadlessServer(quiet(shipped, 'w14'), character_state={})
    me = caller(game, 1, PLAZA, (40, 20))
    game.step(TICK_HZ)
    me.session.voice_tick = game.clock.now_tick() + (w.ARRIVAL_SECONDS + 5) * TICK_HZ - 20 * TICK_HZ
    place(game, me, ROW, (40, 20))
    assert seconds(game, me, w.ARRIVAL_SECONDS + 10) == []
    assert 'w14' not in player(game, me).progress.get('walls', ())


def test_jack_in_and_jack_out_at_a_row_terminal_both_reach_the_caller(shipped):
    game = HeadlessServer(quiet(shipped, 'w12', 'w13'), character_state={})
    me = caller(game, 1, ROW, (19, 16))
    game.step(TICK_HZ)
    act(game, me, Frame('key', {'k': 'j', 't': 0}))
    game.step(3)
    game.commit_commerce()
    assert body(game, me).stance == 'jacked'
    got = heard(game, me) + seconds(game, me, 2)
    assert [ln['text'] for ln in got] == ["You came down. Most of them stop at the door.",
                                          "Cell log: one visitor. Two entries."]
    seconds(game, me, 25)  # Past P8's budget.
    game.core.lattice.jack_out(me.session)
    game.step(3)
    game.commit_commerce()
    assert body(game, me).stance != 'jacked'
    got = heard(game, me) + seconds(game, me, 2)
    assert [ln['text'] for ln in got] == ["Back in your body. It waited. They usually do."]


def test_a_death_decant_says_the_first_eligible_line_in_file_order(shipped):
    from karst41.server.rules import combat as rules
    game = HeadlessServer(quiet(shipped, 'w10', 'w11'), character_state={})
    me = caller(game, 1, ROW, (40, 10))
    attacker = make_actor(game.core.loop.content.npcs['npc-sodium-regular'], 'r', {'spawner': 'r', 'home': (41, 10)})
    game.core.world.enter(attacker, ROW, (41, 10))
    game.core.combat.hurt(attacker, body(game, me), 10_000)
    game.step(rules.DOWN_SECONDS * TICK_HZ)
    game.commit_commerce()
    game.step(rules.DECANT_SECONDS * TICK_HZ)
    game.commit_commerce()
    game.step()
    zone = body(game, me).zone
    # The vat line opens a ten-second danger hold at the decant; the line follows it.
    assert heard(game, me) + seconds(game, me, 9) == []
    got = seconds(game, me, 4)
    first = "Again. You look the same. You always look the same to them." if zone == HALL else \
        "You came back. They get easier. That's the part I'd mind."
    assert [ln['text'] for ln in got] == [first]


def test_w10_then_w11_on_two_decants_in_the_hall(shipped):
    game = HeadlessServer(quiet(shipped, 'w10', 'w11'), character_state={})
    me = caller(game, 1, HALL, (6, 5))
    game.step(TICK_HZ)
    walls(game).event(me.session.player_id, 'death_decant')
    game.commit_commerce()
    seconds(game, me, 25)
    walls(game).event(me.session.player_id, 'death_decant')
    game.commit_commerce()
    assert player(game, me).progress['walls'] == ['w10', 'w11']


# --- rarity ------------------------------------------------------------------------

def test_once_per_character_kept_in_progress_through_the_commerce_queue(shipped):
    game = HeadlessServer(quiet(shipped, 'w16'), character_state={})
    me = caller(game, 1)
    assert len(seconds(game, me, 31)) == 1
    assert 'w16' in walls(game).writing.get(me.session.player_id, ()) or game.core.commerce.outbox
    game.commit_commerce()
    assert player(game, me).progress['walls'] == ['w16']
    act(game, me, Frame('intent', {'name': 'resync', 'args': {}}))  # A new idle stretch.
    assert seconds(game, me, 35) == []
    game.core.sessions.disconnect(me.session)
    game.step(70 * TICK_HZ)  # Past the reconnect window: a new login.
    again = game.connect({'user_id': 1, 'handle': 'Wall1', 'node_name': 'S48d'})
    game.step(2)
    game.frames_for(again)
    assert seconds(game, again, 35) == []


def test_three_per_evening_carried_across_a_reconnect_and_reset_by_a_new_login(shipped):
    content = quiet(shipped)
    content.walls = replace(content.walls, lines=tuple(
        line(f'i{n}', 'idle', f'Idle line {n}.', zones=(), count=1 + 21 * n) for n in range(5)))  # 21 s apart: past P8's budget.
    game = HeadlessServer(content, character_state={})
    me = caller(game, 1)

    def got(client, n):
        out = []
        for _ in range(n):
            game.step(TICK_HZ)
            game.commit_commerce()
            out += [ln['text'] for f in game.frames_for(client) if f.type == 'log' and not f.data.get('replay')
                    for ln in f.data['lines'] if ln['text'].startswith('Idle line')]
        return out
    assert got(me, 100) == ['Idle line 0.', 'Idle line 1.', 'Idle line 2.']  # Then the cap holds.
    game.core.sessions.disconnect(me.session)
    game.step(TICK_HZ)
    again = game.connect({'user_id': 1, 'handle': 'Wall1', 'node_name': 'S48d'})
    assert again.session.walls_heard == 3  # A reconnect is the same evening.
    game.step(2)
    assert got(again, 100) == []
    game.core.sessions.disconnect(again.session)
    game.step(70 * TICK_HZ)
    third = game.connect({'user_id': 1, 'handle': 'Wall1', 'node_name': 'S48d'})
    assert third.session.walls_heard == 0  # A new login is a new evening.
    game.step(2)
    assert got(third, 100) == ['Idle line 3.', 'Idle line 4.']  # Once per character: 0-2 never again.


def test_the_off_switch_silences_the_layer_and_counts_nothing(shipped):
    game = HeadlessServer(quiet(shipped, enabled=False), character_state={})
    me = caller(game, 1, ROW, (90, 26))
    for _ in range(4):
        move(game, me, 'down')
    assert seconds(game, me, 100) == []
    assert walls(game).ears == {}


# --- the gate: never during danger, the opening or an unread body --------------------

def test_refused_in_the_danger_hold_and_said_once_it_ends(shipped):
    """A danger line inside the 10 s hold is never pushed out of view."""
    game = HeadlessServer(quiet(shipped, 'w18'), character_state={})
    me = caller(game, 1, ROW, (80, 20))
    game.step(TICK_HZ)
    me.session.emit('Danger.', kind='danger', channel='zone', role='log_danger', at=game.clock.now_utc().isoformat())
    place(game, me, ROW, (90, 26))
    got = seconds(game, me, 8)
    assert got == [] and 'w18' not in player(game, me).progress.get('walls', ())
    assert [ln['text'] for ln in seconds(game, me, 4)][:1] == ["That sign never goes out. People think it's luck."]


def test_refused_while_threatened_or_travelling(shipped):
    game = HeadlessServer(quiet(shipped, 'w16'), character_state={})
    me = caller(game, 1, ROW, (26, 6))
    hostile = make_actor(game.core.loop.content.npcs['npc-wake-drone'], 'drone', {'home': (24, 6)})
    hostile.marked = True
    game.core.world.enter(hostile, ROW, (24, 6))
    body(game, me).target = hostile.id
    assert seconds(game, me, 40) == []
    game.core.world.leave(hostile)
    body(game, me).target = None
    avatar = game.core.characters.sim.avatars[me.session.user_id]
    avatar.travel, travel = object(), avatar.travel
    assert not walls(game).attempt(me.session, game.clock.now_tick())
    avatar.travel = travel
    assert [ln['text'] for ln in seconds(game, me, 2)] == ["You've stopped. Everyone else here is pretending not to."]


def test_silent_for_an_unread_body_and_while_the_opening_runs(shipped):
    game = HeadlessServer(quiet(shipped, 'w16', 'w01'), character_state={})
    unread = game.connect({'user_id': 1, 'handle': 'Unread', 'node_name': 'S48d'})
    game.step(2)
    place(game, unread, ROW, (60, 20))
    game.frames_for(unread)
    assert seconds(game, unread, 40) == []
    me = caller(game, 2)
    game.core.opening.states[me.session.id] = fresh()  # A live runner for this character.
    assert seconds(game, me, 40) == []
    game.core.opening.finish(game.core.opening.states[me.session.id])
    assert len(seconds(game, me, 25)) == 1  # Once the runner ends (its first line spent P8's budget).


# --- fit and durability --------------------------------------------------------------

def test_a_pair_that_does_not_fit_drops_both_and_stays_unfired(shipped):
    content = quiet(shipped)
    long = 'A line that runs on and on ' * 3
    content.walls = replace(content.walls, lines=(line('x', 'idle', long, answer=long, count=1),))
    game = HeadlessServer(content, character_state={})
    me = caller(game, 1)
    assert not w.fits(long, long)
    got = [ln for _ in range(5) for f in (game.step(TICK_HZ), game.frames_for(me))[1] if f.type == 'log'
           for ln in f.data['lines'] if 'runs on' in ln['text']]
    assert got == [] and me.session.walls_heard == 0
    assert not walls(game).writing.get(me.session.player_id)


def test_a_full_commerce_queue_says_nothing_and_spends_no_budget(shipped):
    game = HeadlessServer(quiet(shipped, 'w16'), character_state={})
    me = caller(game, 1)
    commerce = game.core.commerce
    commerce.pending, commerce.waiting = True, [lambda: None] * QUEUE_LIMIT
    before = me.session.voice_tick
    assert seconds(game, me, 35) == []
    assert me.session.voice_tick == before and 'w16' not in player(game, me).progress.get('walls', ())
    commerce.pending, commerce.waiting = False, []
    assert len(seconds(game, me, 2)) == 1


def test_death_decants_window_follows_p8s_danger_hold():
    """Technical lead: derived from the hold, so a change to it can't starve the trigger."""
    from karst41.server.danger import DANGER_HOLD_SECONDS
    assert w.EVENT_SECONDS['death_decant'] == DANGER_HOLD_SECONDS + w.DECANT_GRACE_SECONDS
    assert w.EVENT_SECONDS['death_decant'] > DANGER_HOLD_SECONDS


def test_p8_ambience_and_a_wall_line_share_one_budget(shipped):
    """A wall line and the Row's own ambience under P8's one budget: whichever
    comes first, the other waits the budget out, and both reach the caller."""
    content = quiet(shipped, 'w18')
    game = HeadlessServer(content, character_state={})
    me = caller(game, 1, ROW, (80, 20))
    ambience = game.core.dialogue.ambience
    texts = set(content.looks[ROW].ambient)

    def log(n):
        out = []
        for _ in range(n):
            game.step(TICK_HZ)
            out += [(game.clock.now_tick(), ln['text']) for f in game.frames_for(me)
                    if f.type == 'log' and not f.data.get('replay') for ln in f.data['lines']
                    if ln['kind'] == 'ambient']
        return out
    log(2)
    ambience.zones[ROW].ambient_due = game.clock.now_tick() + 1  # The Row's ambience first.
    first = log(2)
    assert len(first) == 1 and first[0][1] in texts
    place(game, me, ROW, (90, 26))  # Then into W18's region: the budget holds it.
    early = log(15)
    assert not [t for _, t in early if t not in texts]
    later = log(10)
    wall = [(tick, t) for tick, t in later if t == content.walls.lines[0].text]
    assert wall and wall[0][0] - first[0][0] >= 20 * TICK_HZ  # P8's budget, shared.
    # And the other way round: inside the budget after the wall line, the
    # ambience is dropped (P8 drops, never queues); once it reopens, it comes.
    ambience.zones[ROW].ambient_due = game.clock.now_tick()
    assert not [t for _, t in log(15) if t in texts]
    log(5)
    ambience.zones[ROW].ambient_due = game.clock.now_tick()
    assert [t for _, t in log(2) if t in texts]


def test_stepping_out_of_the_tin_halo_is_a_row_arrival(shipped):
    """Creative lead: the first arrival after Vesper's page is usually out of the Halo."""
    game = HeadlessServer(quiet(shipped, 'w14'), character_state={})
    me = caller(game, 1, 'sodium-tin-halo', (2, 3))
    move(game, me, 'up')  # Onto the door back to the Row (2, 2), then E, as a caller does.
    game.step(5)
    act(game, me, Frame('key', {'k': 'e', 't': 0}))
    got = []
    for _ in range(40):
        game.step(TICK_HZ)
        got += heard(game, me)
        if got:
            break
    assert body(game, me).zone == ROW
    assert [ln['text'] for ln in got] == ["There you are. It's loud here. I'll keep it short."]
