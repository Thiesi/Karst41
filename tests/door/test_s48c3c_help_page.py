"""S48c3c, terminal UI 10: help's first page in a zone is This screen.

The keys in use, from the caller's keymap, and a legend built only from
drawn cells under the names HERE and NEARBY use. Every entry gives glyph and
word. It fits one 80x24 screen and never scrolls: an overflowing legend ends
with `+n more` and continues on the next page; the existing pages follow.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from karst41.content.loader import load_content
from karst41.door.model import Display
from karst41.door.present.zone import screen_legend
from karst41.door.screens import help_pages, lease_line, this_screen
from karst41.place import CHARTED_GROUND
from karst41.server.protocol import Frame
from tests.character_helpers import create, place
from tests.harness import HeadlessServer

def keymap(repo_root: Path) -> dict:
    from dataclasses import asdict
    return asdict(load_content(repo_root/'src/karst41/content').keymaps)


def body(grid, legend, *, remembered=None, charted=None, actors=(), nearby=(), objects=()) -> dict:
    return {'viewport': {'x': 0, 'y': 0, 'w': len(grid[0]), 'h': len(grid)}, 'tiles': grid, 'legend': legend,
            'remembered': remembered or [], 'charted': charted or [], 'actors': list(actors),
            'nearby': list(nearby), 'objects': list(objects)}


LEGEND = [{'id': 'floor', 'glyph': '·'}, {'id': 'exit', 'glyph': '>'}, {'id': 'wall', 'glyph': '#'}]
ME = {'id': 'me', 'x': 0, 'y': 0, 'role': 'player_self', 'class': 'caller'}


def test_only_drawn_cells_reach_the_legend():
    # Exit at (3,0): unseen (-1), charted ground only, then visible.
    unseen = body([[0, 0, 0, -1]], LEGEND, actors=[ME])
    charted = body([[0, 0, 0, -3]], LEGEND, charted=[[0, 0, 0, CHARTED_GROUND]], actors=[ME])
    seen = body([[0, 0, 0, 1]], LEGEND, actors=[ME])
    assert ('»', 'way out') not in screen_legend(unseen)
    assert ('»', 'way out') not in screen_legend(charted)  # Charted ground carries no tile class.
    assert ('»', 'way out') in screen_legend(seen)


def test_actors_use_their_presented_nearby_names_and_glyphs():
    npc = {'id': 'n1', 'x': 2, 'y': 0, 'class': 'civilian', 'glyph_id': 'actor-npc'}
    drone = {'id': 'd1', 'x': 1, 'y': 0, 'class': 'drone', 'glyph_id': 'actor-drone'}
    unlisted = {'id': 'u1', 'x': 3, 'y': 0, 'class': 'civilian', 'glyph_id': 'actor-npc'}
    legend = screen_legend(body([[0, 0, 0, 0]], LEGEND, actors=[ME, npc, drone, unlisted],
                                nearby=[{'id': 'n1', 'name': 'Sodium regular'},
                                        {'id': 'd1', 'name': 'Training drone'}]))
    assert legend[:3] == [('@', 'you'), ('τ', 'Training drone'), ('☻', 'Sodium regular')]  # Nearest first.
    assert all(word for _, word in legend) and all(glyph for glyph, _ in legend)  # Glyph and word, always.
    assert len(legend) == 3  # An actor NEARBY doesn't name isn't named here either.


def test_page_one_fits_never_scrolls_and_overflows_to_page_two(repo_root):
    many = [(chr(0x41 + i), f'thing {i}') for i in range(20)]
    page, rest = this_screen(many, keymap(repo_root), 'arrows', 76, 15, lease_line('day 1/90'))
    assert len(page) == 15 and page[0] == 'THIS SCREEN' and page[-1] == lease_line('day 1/90')
    assert any(line.startswith('+') and line.endswith(' more') for line in page)
    assert rest[0] == 'IN VIEW (all)' and rest[1:21] == [f'{g}  {w}' for g, w in many]
    page, rest = this_screen(many[:3], keymap(repo_root), 'arrows', 76, 15)
    assert rest == [] and not any(line.endswith(' more') for line in page)


def test_keys_come_from_the_callers_keymap(repo_root):
    arrows, _ = this_screen([], keymap(repo_root), 'arrows', 76, 15)
    vi, _ = this_screen([], keymap(repo_root), 'vi', 76, 15)
    assert '↑↓←→ walk' in ' '.join(arrows) and 'J jack in/out' in ' '.join(arrows)
    # vi letters keep their case: `k` walks up, `K` runs up.
    assert 'kjhl walk' in ' '.join(vi) and 'KJHL run' in ' '.join(vi) and 'f fire / attack' in ' '.join(vi)
    assert 'Ctrl-O jack in/out' in ' '.join(vi)
    assert all(len(line) <= 76 for line in arrows + vi)


@pytest.mark.parametrize('size', [(80, 24), (132, 50)])
def test_question_mark_opens_this_screen_then_the_existing_pages(info, repo_root, size):
    width, height = size
    content = load_content(repo_root/'src/karst41/content')
    game = HeadlessServer(content, character_state={})
    client = game.connect(width=width, height=height)
    game.step(2)
    place(game, client, 'vatside-wake-hall', (6, 6))
    client.session.needs_full_view = True
    game.step(2)
    model = Display(replace(info, width=width, height=height), presentation=True)
    for frame in game.frames_for(client):
        model.receive(frame, 1)
    model.key('?', 2)
    rows = model.layout.text_area.height
    lines = model.local_lines
    # The opening's own state: an unread body on its first day.
    assert lines[0] == 'THIS SCREEN'
    assert 'day 1/90  Under lease: day 1 of 90. No renting or joining yet' in lines[:rows]
    assert '│─└  painted line' in lines[:rows] and 'Θ  decant vat' in lines[:rows]
    tail = help_pages(model.keymap)
    assert lines[-len(tail):] == tail  # The existing pages follow unchanged.


def test_a_covered_npc_shows_only_its_cover_in_the_legend(info, repo_root):
    content = load_content(repo_root/'src/karst41/content')
    game = HeadlessServer(content, character_state={})
    client = game.connect({'user_id': 1, 'handle': 'Cover', 'node_name': 'P4'}, width=80, height=24)
    create(game, client, archetype=3, name='Cover', card='none')
    place(game, client, 'sodium-row', game.core.world.free_tile('sodium-row', (89, 3)))
    zone = game.core.world.zones['sodium-row']
    npc = next(a for a in zone.actors.values() if a.behaviour.get('template') == 'npc-plainclothes-warden')
    game.core.world.enter(npc, zone.id, game.core.world.free_tile(zone.id, (90, 3)))
    client.session.needs_full_view = True
    game.step(2)
    model = Display(replace(info, width=80, height=24), presentation=True)
    for frame in game.frames_for(client):
        model.receive(frame, 1)
    model.prompt = None
    model.key('?', 2)
    text = '\n'.join(str(line) for line in model.local_lines)
    assert 'Sodium regular' in text and 'Warden' not in text and 'Plainclothes' not in text


def test_a_remembered_cell_is_named_from_memory_not_live_state():
    # The door at (3,0) is out of sight: the grid says "remembered" (-2) and
    # memory holds its class as last seen (an open door). Live object state is
    # only ever sent for objects in sight, so a door closed since then can't
    # rename the remembered cell.
    legend = [{'id': 'floor', 'glyph': '·'}, {'id': 'door', 'glyph': '+'}, {'id': 'locked-door', 'glyph': '='}]
    remembered_open = body([[0, 0, 0, -2]], legend, remembered=[[0, 0, 0, 1]], actors=[ME])
    assert ('+', 'door inside a place') in screen_legend(remembered_open)
    assert ('≡', 'locked door') not in screen_legend(remembered_open)
    # The same door in sight and now shut is named from its live state.
    shut = body([[0, 0, 0, 1]], legend, actors=[ME],
                objects=[{'id': 'd', 'x': 3, 'y': 0, 'kind': 'door', 'state': {'open': False}}])
    assert ('≡', 'door inside a place') in screen_legend(shut)
    # A cell remembered as locked stays locked in the legend.
    remembered_locked = body([[0, 0, 0, -2]], legend, remembered=[[0, 0, 0, 2]], actors=[ME])
    assert ('≡', 'locked door') in screen_legend(remembered_locked)


@pytest.mark.parametrize(('me', 'line'), [
    ({'lease_days': 0, 'grade': None}, 'day 1/90  Under lease: day 1 of 90. No renting or joining yet'),
    ({'lease_days': 11, 'grade': 4}, 'day 12/90  Under lease: day 12 of 90. No renting or joining yet'),
    ({'lease_days': 11, 'grade': 5}, 'sig 12/90  Signatory: you may rent and join'),
    ({'lease_days': 40, 'grade': 6, 'legal_at': '2026-11-18T00:00:00+00:00'}, 'on the roll  Your name is on the civic roll'),
])
def test_the_legend_names_the_callers_own_lease_mark(me, line):
    # Terminal UI 10 (#265), with the 2.4 lease row's precedence and numbers.
    from karst41.door.present.zone import lease_row
    assert lease_line(lease_row(me)) == line
