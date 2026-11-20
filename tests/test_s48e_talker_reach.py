"""S48e finding 1: talk-page actions reach through the talker.

Technical lead's ruling (option B): a Work board, Trade or service chosen on
a talk page the caller opened in person passes reach when the caller is
within one tile of the talker or of the counter. It resolves to the counter
bound to that talker in the same zone, and the menu it opens stays in reach
while the caller stays on the tile they chose it from. Every other check is
unchanged. The reach is held in `Commerce.reach`, outside the menu records
P3 hashes, so these tests are its only guard: every close path, disconnect,
a stale grant after leaving the tile or the zone, and the board's trade.
"""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from karst41.content.loader import load_content
from karst41.server import truce as truce_rules
from karst41.server.protocol import Frame
from karst41.server.rules.combat import MARKED_SECONDS
from tests.character_helpers import act, create, place
from tests.harness import HeadlessServer
from tests.test_dialogue_runtime import press

HALO, COUNTER = 'sodium-tin-halo', (8, 15)
VESPER = 'npc-vendor-sodium-tin-halo-vesper'
PRECINCT, QUARTERMASTER = 'core-precinct', (5, 3)
LINDGREN = 'npc-vendor-core-precinct-warden-quartermaster'
REGION = [(x, y) for x in range(7, 10) for y in range(14, 17) if (x, y) != COUNTER]  # Vesper's spawn region.


@pytest.fixture(scope='module')
def content():
    return load_content(Path(__file__).parents[1]/'src/karst41/content')


def apart(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(abs(a[0]-b[0]), abs(a[1]-b[1]))


def off_counter(talker: tuple[int, int], counter: tuple[int, int] = COUNTER) -> list[tuple[int, int]]:
    """Tiles next to the talker but not next to the counter."""
    return [(talker[0]+dx, talker[1]+dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1)
            if (dx or dy) and (talker[0]+dx, talker[1]+dy) != counter
            and apart((talker[0]+dx, talker[1]+dy), counter) == 2]


def hold(game: HeadlessServer, actor, tile: tuple[int, int]) -> None:
    """Put a talker on a tile of its region and keep it there (its wander, decided)."""
    zone = game.core.world.zones[actor.zone]
    for other in list(zone.actors.values()):
        if other is not actor and other.position == tile:
            game.core.world.enter(other, zone.id, game.core.world.free_tile(zone.id, (20, 10), other))
    game.core.world.enter(actor, actor.zone, tile)
    actor.behaviour.update(home=tile, pause=1 << 40)


def at(content, zone_id: str, template: str, talker: tuple[int, int], caller: tuple[int, int]):
    game = HeadlessServer(content, character_state={})
    client = game.connect({'user_id': 1, 'handle': 'Reach', 'node_name': 'S48e'})
    create(game, client, card='none')
    zone = game.core.world.zones[zone_id]
    place(game, client, zone_id, zone.template.meta.spawn or next(e.tile for e in zone.template.meta.exits))
    game.step(30)  # Spawners fill the zone.
    npc = next(a for a in zone.actors.values() if a.behaviour.get('template') == template)
    hold(game, npc, talker)
    for other in list(zone.actors.values()):
        if other.position == caller and other is not npc:
            game.core.world.enter(other, zone.id, game.core.world.free_tile(zone.id, (20, 10), other))
    place(game, client, zone_id, caller)
    game.step(2)
    return game, client, npc


def talk(game: HeadlessServer, client) -> None:
    press(game, client, 'e')
    game.commit_commerce()
    game.step(2)
    assert client.session.id in game.core.dialogue.menus, 'E did not open the talk page'


def toasts(game: HeadlessServer, client) -> list[str]:
    return [f.data['text'] for f in game.frames_for(client) if f.type == 'toast']


def take_board_offer(game: HeadlessServer, client) -> None:
    owner, sid = game.core.contracts, client.session.id
    panel = owner.panels.menus[sid]
    board = {offer.id for offer in owner.offers('vesper')}
    rows = owner.panels.rows(client.session, panel)
    for _ in range(len(rows)):  # Bounded: one press per row at most.
        if rows[panel.cursor]['id'] in board:
            break
        # The door sends a cursor move as this intent (door/model.py), never as a raw key.
        act(game, client, Frame('intent', {'name': 'menu_cursor', 'args': {'delta': 1}}))
        panel = owner.panels.menus[sid]
        rows = owner.panels.rows(client.session, panel)
    assert rows[panel.cursor]['id'] in board, ([r['id'] for r in rows], panel.cursor, sorted(board))
    press(game, client, 'a')
    press(game, client, 'y')
    game.commit_commerce()
    pid = client.session.player_id
    assert any(pid in {p.player for p in job.roster} and not job.story
               for job in owner.state.engine.instances.values()), 'no job taken'


def buy_one(game: HeadlessServer, client) -> str:
    commerce, sid = game.core.commerce, client.session.id
    player = game.core.characters.players[client.session.player_id]
    rows = commerce.body(client.session, commerce.menus[sid])['rows']
    row = min((r for r in rows if r['cells'][2].isdigit()), key=lambda r: int(r['cells'][2]))
    assert int(row['cells'][2]) <= player.chits_hand
    act(game, client, Frame('intent', {'name': 'menu_select', 'args': {'row': row['id'], 'key': 'enter'}}))
    game.commit_commerce()
    held = [game.core.commerce.items[i].template for i in player.inventory]
    assert row['id'] in held, 'the trade did not complete'
    return row['id']


@pytest.mark.parametrize('talker', REGION)
def test_from_every_tile_beside_vesper_but_not_her_counter_the_board_a_job_and_a_trade(content, talker):
    for tile in off_counter(talker):
        game, client, vesper = at(content, HALO, VESPER, talker, tile)
        if not game.core.world.zones[HALO].passable(tile):
            continue
        sid = client.session.id
        talk(game, client)
        press(game, client, 'c')
        panel = game.core.contracts.panels.menus.get(sid)
        assert panel is not None and panel.target == 'vesper', (talker, tile, toasts(game, client))
        assert game.core.commerce.reach[sid] == (HALO, 'vesper', tile)
        take_board_offer(game, client)
        # Vesper wanders to the far side of her region mid-board: still in reach.
        far = max((t for t in REGION if t != talker), key=lambda t: (apart(t, tile), t))
        hold(game, vesper, far)
        game.step(2)
        press(game, client, 'v')  # The board's trade carries the reach over.
        assert game.core.commerce.menus[sid].target == 'vesper', (talker, tile, toasts(game, client))
        assert game.core.commerce.reach[sid] == (HALO, 'vesper', tile)
        hold(game, vesper, talker)
        game.step(2)
        buy_one(game, client)


def test_the_first_walk_tile_8_13_reaches_vesper_at_9_14(content):
    # The tile S48e's headless walk stood on (finding 1).
    assert (8, 13) in off_counter((9, 14))
    game, client, _ = at(content, HALO, VESPER, (9, 14), (8, 13))
    talk(game, client)
    press(game, client, 'v')
    assert game.core.commerce.menus[client.session.id].kind == 'vendor'
    buy_one(game, client)


def test_lindgren_at_4_2_trades_with_a_caller_at_3_1(content):
    assert apart((3, 1), QUARTERMASTER) == 2 and apart((3, 1), (4, 2)) == 1
    game, client, _ = at(content, PRECINCT, LINDGREN, (4, 2), (3, 1))
    talk(game, client)
    press(game, client, 'v')
    menu = game.core.commerce.menus.get(client.session.id)
    assert menu is not None and menu.target == 'warden-quartermaster', toasts(game, client)
    assert game.core.commerce.body(client.session, menu)['rows']
    assert game.core.commerce.reach[client.session.id] == (PRECINCT, 'warden-quartermaster', (3, 1))


# --- refusals: only the distance check changed ------------------------------

@pytest.mark.parametrize('key', ['c', 'v'])
def test_two_tiles_from_both_the_talker_and_the_counter_is_refused(content, key):
    game, client, vesper = at(content, HALO, VESPER, (9, 14), (8, 13))
    talk(game, client)
    hold(game, vesper, (9, 16))  # Wandered off: 3 from the caller; the counter is 2.
    game.frames_for(client)
    press(game, client, key)
    sid = client.session.id
    assert sid not in game.core.contracts.panels.menus and sid not in game.core.commerce.menus
    assert sid not in game.core.commerce.reach
    assert {'c': 'Move next to the counter or cache.',
            'v': 'Stand beside that counter before choosing its terms.'}[key] in toasts(game, client)


@pytest.mark.parametrize('key', ['c', 'v'])
def test_a_counter_in_another_zone_is_never_reached(content, key):
    # Every object bound to Vesper leaves the Halo: her counter and the Halo
    # contract board (38,3), whose contract_handler is also Vesper.
    moved = deepcopy(content)
    halo, row = moved.zones[HALO], moved.zones['sodium-row']
    bound = tuple(o for o in halo.meta.objects
                  if o.id == 'vesper' or o.params.get('contract_handler') == 'vesper')
    assert {o.id for o in bound} >= {'vesper', 'halo-contract-board'}
    moved.zones[HALO] = replace(halo, meta=replace(halo.meta, objects=tuple(
        o for o in halo.meta.objects if o not in bound)))
    moved.zones['sodium-row'] = replace(row, meta=replace(row.meta, objects=(*row.meta.objects, *bound)))
    game, client, _ = at(moved, HALO, VESPER, (9, 14), (8, 13))
    talk(game, client)
    press(game, client, key)
    sid = client.session.id
    assert sid not in game.core.contracts.panels.menus and sid not in game.core.commerce.menus
    assert sid not in game.core.commerce.reach


@pytest.mark.parametrize('key', ['c', 'v'])
def test_a_bound_counter_more_than_two_from_the_talker_is_not_hers(content, key):
    # Technical lead's cap: with Vesper's counter gone, the Halo contract
    # board at (38,3) still names her as handler, but it is not her counter.
    moved = deepcopy(content)
    halo, row = moved.zones[HALO], moved.zones['sodium-row']
    counter = next(o for o in halo.meta.objects if o.id == 'vesper')
    board = next(o for o in halo.meta.objects if o.id == 'halo-contract-board')
    assert board.params.get('contract_handler') == 'vesper' and board.tile == (38, 3)
    moved.zones[HALO] = replace(halo, meta=replace(halo.meta, objects=tuple(
        o for o in halo.meta.objects if o.id != 'vesper')))
    moved.zones['sodium-row'] = replace(row, meta=replace(row.meta, objects=(*row.meta.objects, counter)))
    game, client, _ = at(moved, HALO, VESPER, (9, 14), (8, 13))
    talk(game, client)
    game.frames_for(client)
    press(game, client, key)
    sid = client.session.id
    assert sid not in game.core.contracts.panels.menus and sid not in game.core.commerce.menus
    assert sid not in game.core.commerce.reach
    assert {'c': 'Move next to the counter or cache.',
            'v': 'Stand beside that counter before choosing its terms.'}[key] in toasts(game, client)


def test_the_validator_keeps_counter_bound_talkers_within_two_of_their_counter(content):
    from karst41.content.validate import validate
    assert not validate(content)
    halo = content.zones[HALO]
    spawner = next(sp for sp in halo.meta.spawners if sp.npc == 'npc-vendor-sodium-tin-halo-vesper')
    assert spawner.region is not None
    far = replace(spawner.region, x=spawner.region.x+3)  # Every tile 3+ from (8,15) at x=11.
    moved = deepcopy(content)
    moved.zones[HALO] = replace(halo, meta=replace(halo.meta, spawners=tuple(
        replace(sp, region=far) if sp.id == spawner.id else sp for sp in halo.meta.spawners)))
    problems = [str(p) for p in validate(moved) if 'more than 2 tiles' in str(p)]
    assert problems and all(spawner.id in p for p in problems), problems


@pytest.mark.parametrize('key', ['c', 'v'])
def test_marked_stays_closed_through_the_talker(content, key):
    game, client, _ = at(content, HALO, VESPER, (9, 14), (8, 13))
    talk(game, client)
    player = game.core.characters.players[client.session.player_id]
    player.marked_until = truce_rules.iso(game.core.combat.now() + MARKED_SECONDS)
    game.frames_for(client)
    press(game, client, key)
    sid = client.session.id
    assert sid not in game.core.contracts.panels.menus and sid not in game.core.commerce.menus
    assert sid not in game.core.commerce.reach
    assert any(text.startswith('Vesper: "Not with that name.') for text in toasts(game, client))


@pytest.mark.parametrize('key', ['c', 'v'])
def test_resident_only_access_is_still_refused_through_the_talker(content, key):
    game, client, _ = at(content, HALO, VESPER, (9, 14), (8, 13))
    talk(game, client)
    game.core.characters.world.object_access = lambda actor, spec: False
    game.frames_for(client)
    press(game, client, key)
    sid = client.session.id
    assert sid not in game.core.contracts.panels.menus and sid not in game.core.commerce.menus
    assert "Only the resident may use this room's services." in toasts(game, client)


@pytest.mark.parametrize('key', ['c', 'v'])
def test_a_lattice_contact_is_still_refused(content, key):
    game, client, _ = at(content, HALO, VESPER, (9, 14), (8, 13))
    talk(game, client)
    menu = game.core.dialogue.menus[client.session.id]
    menu.contact = replace(menu.contact, lattice=True)
    game.frames_for(client)
    press(game, client, key)
    sid = client.session.id
    assert sid not in game.core.contracts.panels.menus and sid not in game.core.commerce.menus
    assert 'Return to the physical counter to use this service.' in toasts(game, client)


def test_direct_use_of_a_counter_is_unchanged(content):
    game, client, _ = at(content, HALO, VESPER, (9, 14), (8, 13))
    with pytest.raises(ValueError, match='Move next to the counter or cache.'):
        game.core.commerce.nearby(client.session, 'vesper')  # No talk page: no reach.


# --- the grant's life -------------------------------------------------------

def granted(game: HeadlessServer, client, key: str) -> str:
    talk(game, client)
    press(game, client, key)
    assert client.session.id in game.core.commerce.reach
    return client.session.id


@pytest.mark.parametrize('key', ['c', 'v'])
def test_closing_the_menu_ends_the_grant(content, key):
    game, client, _ = at(content, HALO, VESPER, (9, 14), (8, 13))
    sid = granted(game, client, key)
    # Escape on a menu reaches the server as this intent (door/model.py).
    act(game, client, Frame('intent', {'name': 'menu_close', 'args': {}}))
    game.step()
    assert sid not in game.core.contracts.panels.menus and sid not in game.core.commerce.menus
    assert sid not in game.core.commerce.reach


def test_closing_a_clinic_menu_ends_the_grant(content):
    game, client, _ = at(content, HALO, VESPER, (9, 14), (8, 13))
    sid = granted(game, client, 'v')
    game.core.progression.clinics.close(client.session)
    assert sid not in game.core.commerce.reach


@pytest.mark.parametrize('key', ['c', 'v'])
def test_disconnect_ends_the_grant(content, key):
    game, client, _ = at(content, HALO, VESPER, (9, 14), (8, 13))
    sid = granted(game, client, key)
    game.core.sessions.disconnect(client.session)
    game.step()
    assert sid not in game.core.commerce.reach


def test_the_boards_trade_carries_the_grant_and_its_tile(content):
    game, client, _ = at(content, HALO, VESPER, (9, 14), (8, 13))
    sid = granted(game, client, 'c')
    grant = game.core.commerce.reach[sid]
    press(game, client, 'v')
    assert game.core.commerce.menus[sid].kind == 'vendor'
    assert game.core.commerce.reach[sid] == grant


def test_stepping_off_the_tile_ends_the_grant_even_on_coming_back(content):
    game, client, _ = at(content, HALO, VESPER, (9, 14), (8, 13))
    sid = granted(game, client, 'v')
    place(game, client, HALO, (7, 12))
    place(game, client, HALO, (8, 13))
    assert sid not in game.core.commerce.reach
    with pytest.raises(ValueError, match='Move next to the counter or cache.'):
        game.core.commerce.nearby(client.session, 'vesper')


def test_a_stale_grant_after_a_zone_change_refuses(content):
    game, client, _ = at(content, HALO, VESPER, (9, 14), (8, 13))
    sid = granted(game, client, 'v')
    place(game, client, 'sodium-row', (90, 27))
    place(game, client, HALO, (8, 13))
    assert sid not in game.core.commerce.reach
    rows_before = game.core.commerce.menus.get(sid)
    if rows_before is not None:  # A menu left open across the trip is out of reach.
        with pytest.raises(ValueError, match='Move next to the counter or cache.'):
            game.core.commerce.body(client.session, rows_before)
