"""S48c3c: the Wake Hall's place record (first-ten-minutes §3) and street marks (§4.10)."""

from pathlib import Path

import pytest

from karst41.content.loader import load_content


@pytest.fixture
def tree(repo_root: Path):
    return load_content(repo_root/'src/karst41/content')


def painted(look) -> dict[tuple[int, int], str]:
    cells: dict[tuple[int, int], str] = {}
    for mark in look.marks:
        if mark.line:
            (x1, y1), (x2, y2) = mark.line
            for x in range(min(x1, x2), max(x1, x2) + 1):
                for y in range(min(y1, y2), max(y1, y2) + 1):
                    cells[(x, y)] = mark.glyph
        else:
            for i, ch in enumerate(mark.text):
                cells[(mark.at[0] + i, mark.at[1])] = ch
    return cells


def test_the_line_runs_from_the_gurney_to_the_street_door_without_a_gap(tree):
    cells, hall = painted(tree.looks['vatside-wake-hall']), tree.zones['vatside-wake-hall']
    expected = {**{(7, y): '│' for y in range(6, 14)}, (7, 14): '└',
                **{(x, 14): '─' for x in range(8, 20)}, **{(x, 14): '─' for x in range(21, 37)},
                (37, 14): '┐', (37, 15): '│', (37, 16): '└'}
    assert {at: cells.get(at) for at in expected} == expected
    # It touches the corridor door from both sides, with no gap (presentation lead).
    assert hall.grid[14][20] == 'door' and cells[(19, 14)] == cells[(21, 14)] == '─'
    assert (20, 14) not in cells  # The door itself is drawn, never painted over.
    # It ends beside the street door, and every painted cell is floor.
    assert next(e for e in hall.meta.exits if e.id == 'to-clinics').tile == (38, 16)
    assert all(hall.grid[y][x] == 'floor' for x, y in expected)


def test_signs_and_paint_words_sit_where_the_proposal_draws_them(tree):
    look = tree.looks['vatside-wake-hall']
    assert look.theme == 'vat-interior' and not look.regions  # Presentation lead: one interior theme.
    assert {(s.at, s.text) for s in look.signs} == {((2, 0), ' DECANT '), ((22, 11), ' RANGE '), ((31, 11), ' ISSUE ')}
    words = {(m.at, m.text) for m in look.marks if not m.line and len(m.text) > 1}
    assert words == {((9, 11), 'CARDS'), ((24, 17), 'CLINIC ROW →')}


def test_only_the_first_room_is_charted(tree):
    hall = tree.zones['vatside-wake-hall']
    assert hall.meta.charted and [(r.x, r.y, r.w, r.h) for r in hall.meta.public] == [(0, 0, 21, 20)]


@pytest.mark.parametrize(('zone', 'at', 'text'), [('vatside-clinics', (5, 3), 'MERIDIAN PLAZA →'),
                                                  ('core-plaza', (72, 4), '← NEON STAIR'),
                                                  ('core-plaza', (56, 8), 'NEON STAIR ↓')])
def test_the_street_marks_point_the_way(tree, zone, at, text):
    assert any(m.at == at and m.text == text for m in tree.looks[zone].marks)
    assert all(tree.zones[zone].grid[at[1]][at[0] + i] == 'floor' for i in range(len(text)))
