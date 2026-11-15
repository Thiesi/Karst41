"""S48c3b / #261, terminal UI 2 and 6: names are never cut inside a word.

HERE, NEARBY, TARGET, the name row's handle, and the header's zone name, in
the presentation renderer at both sizes.
"""

from dataclasses import replace

import pytest

from karst41.door.cells import CellBuffer, display_width
from karst41.door.chrome import here_label, panel_names, person_word, word_prefix, zone_cut, zone_minimum
from karst41.door.model import Display
from karst41.door.present import zone as drawing
from karst41.server.protocol import Frame
from tests.fake_terminal import FakeTerminal

SIZES = [(80, 24), (132, 50)]


# --- the rules ------------------------------------------------------------

def test_word_prefix_never_cuts_inside_a_word_except_one_too_long_alone():
    assert word_prefix('Dr. Ilse Vantongeren', 16) == 'Dr. Ilse...'
    assert word_prefix('Fire here and Sable comes. No Halo.', 20) == 'Fire here and...'
    assert word_prefix('Supercalifragilistic', 16) == 'Supercalifrag...'  # The one in-word cut.
    assert display_width(word_prefix('Supercalifragilistic', 16)) == 16


@pytest.mark.parametrize(('label', 'shown'), [
    ('Hall terminal', 'Hall terminal'),  # Fits whole.
    ('Public terminal (Plaza 1)', 'terminal (Plaza 1)'),  # #261's before/after.
    ('Public terminal (Meridian Plaza north)', 'terminal'),  # The parenthetical doesn't fit: the word alone.
    ('Locker 7 (Clinic Row annex west)', 'Locker'),  # Digit-only words are never chosen.
])
def test_here_label_steps(label, shown):
    assert here_label(label, 18) == shown


def test_here_label_falls_back_to_a_whole_word_prefix():
    # The short form `booth` fits, so step 3 wins over the prefix.
    assert here_label('Extraordinarily-long-wordmark booth', 18) == 'booth'
    # The last word is too long for the field on its own: the prefix.
    assert here_label('Booth of the Extraordinarily-long-wordmark', 18) == 'Booth of the...'


def test_here_row_keeps_its_shape_and_a_right_margin():
    assert drawing.fit_here('§ Public terminal (Plaza 1)', 20) == '§ terminal (Plaza 1)'
    assert display_width(drawing.fit_here('§ Public terminal (Plaza 1)', 20)) == 20  # 22 - indent - margin.


def test_person_word_skips_honorifics_initials_and_digits():
    assert person_word('Dr. Ilse Vantongeren') == 'Vantongeren'
    assert person_word('Ilse V.') == 'Ilse'
    assert person_word('Mr. Reyes 2') == 'Reyes'
    assert person_word('Dr. X.') == ''  # No word left to choose.


def test_panel_names_short_forms_and_collisions():
    sixteen = [16, 16, 16, 16]
    # #261's before/after, and equal full names unaffected.
    assert panel_names(['Dr. Ilse Vantongeren', 'Sablier orderly', 'Sablier orderly', 'Okafor'], sixteen) == \
        ['Vantongeren', 'Sablier orderly', 'Sablier orderly', 'Okafor']
    # Two different names whose short forms would read the same: both prefix.
    assert panel_names(['Dr. Ilse Vantongeren', 'Mr. Kim Vantongeren'], [16, 16]) == ['Dr. Ilse...', 'Mr. Kim...']
    # A short form equal to another row's full name: only the shortened one moves.
    assert panel_names(['Dr. Ilse Vantongeren', 'Vantongeren'], [16, 16]) == ['Dr. Ilse...', 'Vantongeren']
    # A name with no word left to choose uses the prefix.
    assert panel_names(['Dr. Q. 0000000000000'], [16]) == ['Dr. Q....']


def test_zone_cut_keeps_its_first_word_and_drops_a_leading_article():
    assert zone_cut('Sodium Row', 10) == 'Sodium Row'
    assert zone_cut('Sodium Row', 9) == 'Sodium...'
    assert zone_cut('The Tin Halo', 8) == 'Tin...'
    assert zone_minimum('The Tin Halo') == 'Tin...' and zone_minimum('Meridian Plaza') == 'Meridian...'
    assert zone_cut('Sodium Row', 5) == 'So...'  # Threat words come first: the first word cut inside.


# --- the panel ------------------------------------------------------------

def body(**extra) -> dict:
    me = {'zone_name': 'Meridian Plaza', 'zone': 'core-plaza', 'safety': 'safe', 'clock': '21:00',
          'name': 'Measure', 'handle': 'Measure', 'grade': 1, 'hp': 10, 'hp_max': 10, 'sta': 10, 'sta_max': 10,
          'chits': 200, 'lease_days': 0, 'legal_at': None}
    me.update(extra.pop('me', {}))
    return {'me': me, 'hint': '', 'tiles': [], 'actors': [], 'nearby': [], **extra}


def panel_text(info, size, data: dict) -> list[str]:
    width, height = size
    display = Display(replace(info, width=width, height=height), presentation=True)
    display.receive(Frame('view', {'rev': 1, 'kind': 'zone', 'body': data}), 0)
    buffer = CellBuffer(width, height, display.palette.style())
    drawing.panel(buffer, display.body, display.layout, display.paint)
    terminal = FakeTerminal(width, height)
    terminal.write(buffer.diff(None))
    side = display.layout.side
    return [row[side.x:side.x + side.width] for row in terminal.screen_text().splitlines()]


@pytest.mark.parametrize('size', SIZES)
def test_nearby_and_target_rows_shorten_together(info, size):
    nearby = [{'id': 'a', 'name': 'Dr. Ilse Vantongeren', 'relation': '~'},
              {'id': 'b', 'name': 'Sablier orderly', 'relation': '~'}]
    rows = panel_text(info, size, body(nearby=nearby))
    assert any(row.startswith(' ☻ Vantongeren ~') for row in rows), rows
    assert any(row.startswith(' ☻ Sablier orderly ~') for row in rows)
    # TARGET counts too: a target whose short form collides moves both rows.
    target = {'name': 'Mr. Kim Vantongeren', 'hp_percent': 100, 'range': 3, 'relation': 'neutral'}
    rows = panel_text(info, size, body(nearby=nearby, target=target))
    assert any(row.startswith(' Mr. Kim...') for row in rows), rows
    assert any(row.startswith(' ☻ Dr. Ilse... ~') for row in rows), rows
    assert not any('Vant' in row and 'Vantongeren' not in row and '...' not in row for row in rows)


@pytest.mark.parametrize('size', SIZES)
def test_the_lease_row_follows_2_4(info, size):
    def lease(**me) -> str:
        rows = panel_text(info, size, body(me=me))
        return next(row for row in rows if ' ch ' in row or row.strip().endswith(('/90', 'roll')))
    assert lease(lease_days=0).startswith(' 200 ch   day 1/90')  # Column 10; counting from 1 on decant day.
    assert 'day 12/90' in lease(lease_days=11)
    assert 'sig 12/90' in lease(lease_days=11, grade=5)
    assert 'on the roll' in lease(lease_days=11, grade=5, legal_at='2026-11-15T00:00:00+00:00')
    widest = panel_text(info, size, body(me={'chits': 9999, 'lease_days': 88, 'grade': 6}))
    assert any(row.startswith(' 9999 ch  sig 89/90') for row in widest), widest  # 2.4's widest case.
    rows = panel_text(info, size, body())
    assert not any('signatory' in row.lower() for row in rows)  # 2.4: no Signatory XP row.


@pytest.mark.parametrize('size', SIZES)
def test_the_name_row_handle_shows_whole_or_not_at_all(info, size):
    wide = size == (132, 50)

    def name_row(**me) -> str:
        rows = panel_text(info, size, body(me=me))
        return next(row for row in rows if 'G1' in row or 'No. ' in row)
    assert name_row(name='Measure', handle='Mea').startswith(' Measure · Mea' if wide else ' Measure ')
    assert '·' not in name_row(name='Measure', handle='Measure')  # Equal: the name alone.
    long = name_row(name='Measure', handle='Measurement界')
    assert long.startswith(' Measure ') and '·' not in long and '...' not in long  # Dropped, never cut.
    assert 'G1' in long
    gurney = panel_text(info, size, body(me={'name': 'No. 0431', 'handle': 'Wide界', 'grade': None}))
    assert any(row.startswith(' No. 0431') for row in gurney)


# --- the header -----------------------------------------------------------

def header(info, size, me: dict, notice=None) -> str:
    width, height = size
    display = Display(replace(info, width=width, height=height), presentation=True)
    display.receive(Frame('view', {'rev': 1, 'kind': 'zone', 'body': body(me=me)}), 0)
    buffer = CellBuffer(width, height, display.palette.style())
    drawing.head(buffer, display.body, display.layout, display.paint, notice=notice)
    terminal = FakeTerminal(width, height)
    terminal.write(buffer.diff(None))
    return terminal.screen_text().splitlines()[0]


@pytest.mark.parametrize('size', SIZES)
def test_the_header_leaves_out_a_district_that_names_the_zone(info, size):
    top = header(info, size, {'zone_name': 'Sodium Row', 'district': 'Sodium Row', 'safety': 'contested', 'truce': 'sable'})
    assert 'SODIUM ROW' not in top and top.lstrip().startswith('Sodium Row')
    top = header(info, size, {'zone_name': 'The Tin Halo', 'district': 'Sodium Row', 'safety': 'pocket'})
    assert 'SODIUM ROW' in top and 'The Tin Halo' in top  # Different names: unaffected.


def test_the_header_keeps_the_zones_first_word_and_the_notice_whole(info):
    me = {'zone_name': 'Sodium Row', 'district': 'Sodium Row', 'safety': 'contested', 'truce': 'sable', 'hunted': True}
    top = header(info, (80, 24), me, notice=('Fire here and Sable comes. No Halo.', 'log_danger', 0))
    assert top.lstrip().startswith('Sodium...') and 'S...' not in top.replace('Sodium...', '')
    assert 'Fire here and Sable comes. No Halo.' in top
    for word in ('CONTESTED', 'TRUCE', 'PvP G5+', 'HUNTED'):
        assert word in top
    # At 132x50 the full zone fits.
    assert 'Sodium Row' in header(info, (132, 50), me, notice=('Fire here and Sable comes. No Halo.', 'log_danger', 0))


# --- disclosure -------------------------------------------------------------

@pytest.mark.parametrize('size', SIZES)
def test_a_covered_npc_never_shortens_to_its_real_name(info, repo_root, size):
    """Short forms come from the presented name only (terminal UI 6, #261).

    The plainclothes Warden's cover is made long enough to shorten; NEARBY
    and TARGET show the cover's last word and never `Warden`.
    """
    from karst41.content.loader import load_content
    from karst41.server.views.zone_view import Perception, build
    from tests.character_helpers import create, place
    from tests.harness import HeadlessServer
    width, height = size
    content = load_content(repo_root/'src/karst41/content')
    cover = 'Sodium Row night regular'
    for key in ('npc-plainclothes-warden',):
        content.npcs[key] = replace(content.npcs[key], cover_name=cover)
    game = HeadlessServer(content, character_state={})
    client = game.connect({'user_id': 1, 'handle': 'Cover', 'node_name': 'P4'}, width=width, height=height)
    create(game, client, archetype=3, name='Cover', card='none')
    place(game, client, 'sodium-row', game.core.world.free_tile('sodium-row', (89, 3)))
    zone = game.core.world.zones['sodium-row']
    npc = next(a for a in zone.actors.values() if a.behaviour.get('template') == 'npc-plainclothes-warden')
    game.core.world.enter(npc, zone.id, game.core.world.free_tile(zone.id, (90, 3)))
    caller = game.core.world.actors[client.session.player_id]
    caller.target = npc.id
    view = build(caller, zone, Perception(), content, width, height, '12:00', False, 0)
    view['target'] = game.core.combat.target_view(caller)
    assert next(a for a in view['nearby'] if a['id'] == npc.id)['name'] == cover
    assert view['target']['name'] == cover
    rows = panel_text(info, size, {**view, 'me': {**body()['me'], **view['me'], 'grade': 1, 'chits': 0,
                                                     'lease_days': 0, 'legal_at': None}})
    text = '\n'.join(rows)
    assert 'regular' in text and 'Warden' not in text and 'Plainclothes' not in text


def test_threat_words_come_before_the_zones_first_word(info):
    # Terminal UI 2's stated exception: with every threat word showing, a
    # zone whose first word alone is too long is cut inside that word.
    me = {'zone_name': 'Supercalifragilisticexpialidociousness Yard', 'safety': 'contested', 'truce': 'sable',
          'heat_until': 'x', 'marked_until': 'x', 'hunted': True}
    top = header(info, (80, 24), me)
    for word in ('CONTESTED', 'TRUCE', 'PvP G5+', 'HEAT', 'MARKED', 'HUNTED'):
        assert word in top
    first = top.split()[0]
    assert first.startswith('Supercal') and first.endswith('...') and 'Yard' not in top
