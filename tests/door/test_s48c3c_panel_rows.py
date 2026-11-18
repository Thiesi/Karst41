"""S48c3c, terminal UI 2.4: the pinned rows for a Wake before the record.

The name row shows the gurney number, the grade slot stays empty until the
record is read, and the chits and lease row shows from the first frame.
"""

import pytest

from tests.door.test_s48c3b_names import SIZES, body, panel_text


@pytest.mark.parametrize('size', SIZES)
def test_an_unread_body_shows_chits_and_day_but_no_grade(info, size):
    rows = panel_text(info, size, body(me={'name': 'No. 0431', 'handle': 'Wide', 'grade': None,
                                           'chits': 0, 'lease_days': 0}))
    name = next(row for row in rows if row.startswith(' No. 0431'))
    assert 'G' not in name.replace('No. 0431', '')  # The grade slot is empty.
    assert any(row.startswith(' 0 ch     day 1/90') for row in rows), rows


@pytest.mark.parametrize('size', SIZES)
def test_the_grade_appears_with_the_record(info, size):
    rows = panel_text(info, size, body(me={'name': 'Noor', 'grade': 1, 'chits': 200, 'lease_days': 0}))
    assert any(row.startswith(' Noor') and 'G1' in row for row in rows)
    assert any(row.startswith(' 200 ch   day 1/90') for row in rows)


def test_the_real_unread_body_carries_chits_and_no_grade(repo_root):
    from karst41.content.loader import load_content
    from tests.harness import HeadlessServer
    game = HeadlessServer(load_content(repo_root/'src/karst41/content'), character_state={})
    client = game.connect()
    game.step(2)
    me = game.core.characters.sim.views[client.session.id].previous['body']['me']
    assert me['grade'] is None and me['chits'] is not None and me['name'].startswith('No. ')
