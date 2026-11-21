"""S48e: the reward line in display names only (presentation lead, ruling (a)).

The board, the terms and the 132x50 detail showed raw faction ids
("halvard +5, sablier +5, ..."). Now:
- when every faction moves by the same amount, "+5 with every faction";
- otherwise each faction's short name, kept on one row with its number
  ("Halvard +5"), so a bare "+5" never wraps onto a row of its own at 80x24;
- items and programs by name, never by id.
"""

from pathlib import Path
import textwrap

import pytest

from karst41.content.loader import load_content
from karst41.layout import compute
from karst41.server.contracts import panels
from karst41.server.contracts.engine import Payment
from karst41.server.protocol import Frame
from tests.character_helpers import act
from tests.test_s48e_talker_reach import HALO, VESPER, at, talk

SPACE = chr(0xA0)  # The no-break space payment() joins a name and its number with.


@pytest.fixture(scope='module')
def content():
    return load_content(Path(__file__).parents[1]/'src/karst41/content')


def plain(text: str) -> str:
    return text.replace(SPACE, ' ')


def test_every_faction_alike_is_one_phrase(content):
    reward = Payment(200, 100, {fid: 5 for fid in content.factions}, 'explicit')
    assert plain(panels.payment(reward, content)) == '200 chits; 100 XP; +5 with every faction'


def test_factions_that_differ_are_named_and_items_and_programs_too(content):
    standing = {'halvard': 5, 'sable': -3}
    reward = Payment(150, 50, standing, 'explicit', ('wpn_hall_training_pistol',), ('prg_pick',))
    text = plain(panels.payment(reward, content))
    assert text == '150 chits; 50 XP; Halvard +5, Sable -3; Training pistol, Pick'
    # All but one faction is not "every faction".
    most = {fid: 5 for fid in list(content.factions)[1:]}
    text = plain(panels.payment(Payment(1, 1, most, 'explicit'), content))
    assert 'every faction' not in text and 'Sablier +5' in text
    assert not any(fid in text for fid in content.factions)  # No raw id ever (every id is lower case).


def board(content):
    game, client, _ = at(content, HALO, VESPER, (9, 14), (8, 13))
    game.core.contracts.game.players[client.session.player_id].progress['wake_combat'] = 'skipped'
    talk(game, client)
    game.frames_for(client)
    act(game, client, Frame('key', {'k': 'c', 't': 0}))
    game.commit_commerce()
    owner, session = game.core.contracts, client.session
    panel = owner.panels.menus[session.id]
    rows = owner.panels.rows(session, panel)
    panel.cursor = next(n for n, row in enumerate(rows) if row['id'] == 'vesper-first-thing')  # The Story row.
    return owner, session, panel


def test_vespers_first_job_reads_plus_five_with_every_faction_on_her_board(content):
    owner, session, panel = board(content)
    assert (session.caps['width'], session.caps['height']) == (80, 24)
    detail = owner.panels.body(session, panel)['detail']
    assert 'Your solo reward: 200 chits; 100 XP; +5 with every' in detail.replace('\n', ' ')
    assert SPACE not in detail
    assert not any(fid+' +' in detail for fid in content.factions)


def test_at_80x24_each_faction_keeps_its_number_on_its_row(content, monkeypatch):
    owner, session, panel = board(content)
    mixed = Payment(50, 10, {fid: 5 + n for n, fid in enumerate(content.factions)}, 'explicit')
    real = panels.payment
    # These terms are chosen so that a plain wrap would leave "+7" alone at a row's start.
    plain_rows = textwrap.wrap('Your solo reward: '+plain(real(mixed, content)), compute(80, 24).contracts.width)
    assert any(row.startswith('+') for row in plain_rows), plain_rows
    monkeypatch.setattr(panels, 'payment', lambda _, table: real(mixed, table))
    rows = owner.panels.body(session, panel)['detail'].split('\n')
    assert SPACE not in '\n'.join(rows)
    reward = [row for row in rows if row.startswith('Your solo reward')]
    assert reward
    for n, faction in enumerate(content.factions.values()):
        pair = f'{faction.short} {5 + n:+d}'
        assert any(pair in row for row in rows), (pair, rows)
    assert not any(row.lstrip().startswith('+') for row in rows), rows  # No bare number on its own row.
