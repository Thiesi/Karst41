"""#277 (D4): the publishers' focus lookup, cheaper and byte-identical.

- `ContentTree.active` looks records up in a set of every non-active
  bundle's members, rebuilt whenever a bundle object is added, removed or
  replaced. It answers exactly as the original scan for every record.
- `Core.interaction_views` builds the route list once for its E and J
  resolutions instead of once each.
- A city walk publishes the same frames, byte for byte, as the original
  code: zone views with focus and jack_focus, hints, and the lattice view
  while jacked in. Both callers walk, one with the vi keymap (Ctrl-O).
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from karst41.content.loader import ContentTree, load_content
from karst41.content.schema import Bundle
from karst41.server.core import Core
from karst41.server.interaction import ResolvedInteraction
from karst41.server.protocol import Frame, encode
from karst41.server.sessions import Session
from karst41.server.world.sight import private_visible, sees_actor, sight_radius, visible_tiles
from karst41.server.world.simulation import NIGHT_END_HOUR, NIGHT_START_HOUR
from tests.character_helpers import create
from tests.harness import HeadlessServer


ROOT = Path(__file__).parents[2]
# Records the focus path asks `active` about, all live in shipped content: two relays and the hub talkers.
WALK_TOGGLED = {'relays': ('cold-chain', 'block'),
                'dialogue': ('row-enforcer', 'row-street-regular', 'solenne', 'okafor', 'vantongeren', 'sablier-orderly')}


def reference_active(self: ContentTree, kind: str, record_id: str) -> bool:
    """`ContentTree.active` before #277 D4, verbatim."""
    return not any(bundle.status != "active" and record_id in bundle.members.get(kind, ())
                   for bundle in self.bundles.values())


def reference_views(self: Core, session: Session) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    """`Core.interaction_views` before #277 D4, verbatim: each resolution builds its own route list."""
    sim = self.characters.sim if self.characters else self.simulation
    if sim is None or session.user_id not in sim.avatars:
        return None, None
    actor = sim.avatars[session.user_id].actor
    zone = sim.world.zones.get(actor.zone)
    if zone is None:
        return None, None
    hour = self.clock.now_local().hour
    radius = sight_radius(actor, zone, hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR)
    visible = visible_tiles(actor, zone, radius)
    def disclose(result: ResolvedInteraction | None) -> dict[str, str] | None:
        if result is None or result.choice.focus is None:
            return None
        choice = result.choice
        if choice.tile is not None and choice.tile not in visible:
            return None
        if choice.actor_id:
            other = sim.world.actors.get(choice.actor_id)
            if other is None or not private_visible(actor, other) or not sees_actor(actor, other, visible, radius):
                return None
        return choice.focus.record()
    e = Frame('key', {'k': 'e'})
    jack = Frame('key', {'k': 'ctrl-o' if session.keymap_id == 'vi' else 'j'})
    return disclose(self.resolve_interaction(session, e)), disclose(self.resolve_interaction(session, jack))


@pytest.fixture(scope='module')
def content() -> ContentTree:
    return load_content(ROOT/'src/karst41/content')


def every_record(tree: ContentTree) -> list[tuple[str, str]]:
    """Every (kind, id) a bundle lists, every loaded record of those kinds, and ids no record has."""
    kinds = {kind for bundle in tree.bundles.values() for kind in bundle.members}
    pairs = {(kind, member) for bundle in tree.bundles.values() for kind, members in bundle.members.items()
             for member in members}
    for kind in kinds:
        pairs.update((kind, rid) for rid in getattr(tree, kind, {}) or {})
        pairs.add((kind, 'no-such-record'))
    pairs.update(('no-such-kind', member) for _, member in list(pairs)[:50])
    return sorted(pairs)


def test_active_answers_as_the_original_for_every_record(content: ContentTree) -> None:
    pairs = every_record(content)
    assert sum(not reference_active(content, *pair) for pair in pairs) > 100, "authoring bundles are exercised"
    assert [content.active(*pair) for pair in pairs] == [reference_active(content, *pair) for pair in pairs]


def test_active_follows_every_bundle_change(content: ContentTree) -> None:
    tree = deepcopy(content)
    pairs = every_record(tree)

    def same() -> None:
        assert [tree.active(*pair) for pair in pairs] == [reference_active(tree, *pair) for pair in pairs]
    same()
    contract = next(iter(tree.contracts))
    assert tree.active('contracts', contract)
    tree.bundles['d4-authoring'] = Bundle('d4-authoring', 'authoring', {'contracts': (contract,)})  # Added.
    assert not tree.active('contracts', contract)
    same()
    tree.bundles['d4-authoring'] = replace(tree.bundles['d4-authoring'], status='active')  # Replaced.
    assert tree.active('contracts', contract)
    same()
    tree.bundles.pop('d4-authoring')  # Removed.
    authoring = next(bid for bid, bundle in tree.bundles.items() if bundle.status != 'active')
    kind, members = next(iter(tree.bundles[authoring].members.items()))
    assert not tree.active(kind, members[0])
    tree.bundles[authoring] = replace(tree.bundles[authoring], members={**tree.bundles[authoring].members,
                                                                         kind: members[1:]})
    assert tree.active(kind, members[0])
    same()
    tree.bundles = {}  # The whole mapping replaced.
    assert tree.active(kind, members[1])
    same()
    # The copy kept its own answers throughout.
    assert not content.active(kind, members[0]) and not content.active(kind, members[1])


def focus_points(game: HeadlessServer) -> list[tuple[str, list[tuple[int, int]]]]:
    """Per hub zone: every passable tile next to (or on) an object, in sorted order."""
    out = []
    for zid in sorted(game.core.world.zones):
        zone = game.core.world.zones[zid]
        near = {(x+dx, y+dy) for obj in zone.objects.values() for x, y in [obj.spec.tile]
                for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
        out.append((zid, sorted(tile for tile in near if zone.passable(tile))))
    return out


def walk(content: ContentTree, *, reference: bool, monkeypatch: pytest.MonkeyPatch) -> tuple[list[tuple[str, bytes]], dict[str, int]]:
    """The city walk; returns every frame sent to either caller, encoded, in order."""
    sent: list[tuple[str, bytes]] = []
    original_send = Session.send

    def recorded(session: Session, frame: Frame) -> bool:
        sent.append((session.id, encode(frame)))
        return original_send(session, frame)
    with monkeypatch.context() as patch:
        patch.setattr(Session, 'send', recorded)
        if reference:
            patch.setattr(ContentTree, 'active', reference_active)
            patch.setattr(Core, 'interaction_views', reference_views)
        tree = deepcopy(content)  # Each arm starts from the same loaded tree and its own cache.
        game = HeadlessServer(tree, character_state={})
        callers = []
        for n, keymap in enumerate(('arrows', 'vi')):
            caller = game.connect({'user_id': n + 1, 'handle': f'Walker{n + 1}', 'node_name': 'test'})
            create(game, caller, archetype=0, name=f'Walker{n + 1}', card='none')
            caller.session.keymap_id = keymap
            callers.append(caller)
        game.commit_characters()
        game.commit_commerce()
        core = game.core
        jacks = 0
        for zid, tiles in focus_points(game):
            zone = core.world.zones[zid]
            for index, tile in enumerate(tiles):
                # Live records go in and out of an authoring bundle on alternate
                # tiles, so focus depends on `active` and its cache is rebuilt mid-walk.
                if index % 2:
                    tree.bundles['d4-walk'] = Bundle('d4-walk', 'authoring', WALK_TOGGLED)
                else:
                    tree.bundles.pop('d4-walk', None)
                for caller, at in zip(callers, (tile, tiles[index // 2])):  # The second caller trails.
                    avatar = core.characters.sim.avatars[caller.session.user_id]
                    try:
                        if avatar.actor.zone:
                            core.world.zones[avatar.actor.zone].deactivate(caller.session.id, game.clock.now_tick())
                        core.world.enter(avatar.actor, zid, at)
                    except ValueError:  # Occupied: stay where it was.
                        continue
                    finally:
                        avatar.zone = caller.session.subscribed = avatar.actor.zone
                        zone_now = core.world.zones.get(avatar.actor.zone)
                        if zone_now is not None:
                            zone_now.activate(caller.session.id, game.clock.now_tick())
                game.step()
                game.commit_characters()
                game.commit_commerce()
            # Jack in at the zone's first public terminal, ride a few ticks, jack out.
            terminal = next((obj for _, obj in sorted(zone.objects.items()) if obj.spec.kind == 'terminal'), None)
            caller = callers[0]
            if terminal is not None:
                avatar = core.characters.sim.avatars[caller.session.user_id]
                beside = next((t for t in focus_points_for(zone, terminal.spec.tile) if zone.passable(t)), None)
                try:
                    if beside is not None:
                        core.world.enter(avatar.actor, zid, beside)
                except ValueError:
                    beside = None
                if beside is not None:
                    game.step()
                    if core.lattice.jack_interaction(caller.session).focus is not None:
                        jacks += 1
                    caller.send(Frame('key', {'k': 'j', 't': 0}))
                    for _ in range(6):
                        game.step()
                        game.commit_characters()
                    caller.send(Frame('key', {'k': 'j', 't': 0}))
                    for _ in range(6):
                        game.step()
                        game.commit_characters()
        return sent, {'jacks': jacks}


def focus_points_for(zone: Any, tile: tuple[int, int]) -> list[tuple[int, int]]:
    return sorted((tile[0]+dx, tile[1]+dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0))


def test_a_city_walk_publishes_byte_identical_frames(content: ContentTree, monkeypatch: pytest.MonkeyPatch) -> None:
    head, head_stats = walk(content, reference=False, monkeypatch=monkeypatch)
    original, original_stats = walk(content, reference=True, monkeypatch=monkeypatch)
    assert head_stats == original_stats
    text = [frame.decode('utf-8', 'replace') for _, frame in head]
    # The walk exercises what D4 touches: E focus, J focus on both keymaps, and the lattice view.
    assert sum('"focus":{' in t for t in text) > 200, "E focus is published"
    assert sum('"jack_focus":{' in t for t in text) > 20, "J focus is published"
    assert any('Ctrl-O jack in' in t for t in text), "the vi caller's hint names Ctrl-O"
    assert any('"kind":"lattice"' in t for t in text), "a caller jacked in"
    assert head_stats['jacks'] > 0
    assert len(head) == len(original)
    first = next((i for i, (a, b) in enumerate(zip(head, original)) if a != b), None)
    assert first is None, (first, head[first][1][:400], original[first][1][:400])
