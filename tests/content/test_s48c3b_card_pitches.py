"""#242 / entities 1.20 (S48c3b): two pitch lengths per recruiter card, never cut.

The card menu shows `pitch` when it fits the card detail area and the
recruiter's own `pitch_short` otherwise. The validator rejects a card with no
short pitch, a short pitch that doesn't fit 80x24 or a full pitch that doesn't
fit 132x50.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from karst41.content.loader import load_content
from karst41.content.validate import validate
from karst41.layout import card_detail, card_detail_fits, card_detail_size
from karst41.server.wake import cards


@pytest.fixture
def tree(repo_root: Path):
    return load_content(repo_root/'src/karst41/content')


def messages(tree) -> list[str]:
    return [p.message for p in validate(tree)]


def test_shipped_cards_validate_and_both_lengths_fit(tree) -> None:
    assert validate(tree) == []
    assert card_detail_size(80, 24) == (66, 4)
    for card in tree.wake_cards.values():
        assert card.pitch_short == f'faction-{card.id}-pitch-short'
        assert card_detail_fits(tree.text[card.pitch_short].body, 80, 24)
        assert card_detail_fits(tree.text[card.pitch].body, 132, 50)
        assert not card_detail_fits(tree.text[card.pitch].body, 80, 24)  # So 80x24 shows the short one.


@pytest.mark.parametrize(('size', 'field'), [((80, 24), 'pitch_short'), ((132, 50), 'pitch')])
def test_the_menu_shows_the_length_that_fits_whole(tree, size, field) -> None:
    rows = card_detail_size(*size)[1]
    for cursor, card in enumerate(tree.wake_cards.values()):
        detail = cards(tree, cursor, *size)['detail']
        text = tree.text[getattr(card, field)].body
        assert detail == '\n'.join(card_detail(text, *size))
        assert len(detail.rstrip('\n').split('\n')) <= rows
        assert '...' not in detail
        assert 'forty-' not in detail.replace('forty-one', '')  # Hyphenated words stay whole.


def test_a_resize_chooses_again(tree) -> None:
    small, large = cards(tree, 0, 80, 24)['detail'], cards(tree, 0, 132, 50)['detail']
    assert small != large
    assert cards(tree, 0, 80, 24)['detail'] == small


def test_a_card_without_a_short_pitch_is_rejected(tree) -> None:
    tree.wake_cards['halvard'] = replace(tree.wake_cards['halvard'], pitch_short='')
    assert any('needs a pitch_short' in m for m in messages(tree))


def test_a_short_pitch_that_does_not_fit_80x24_is_rejected(tree) -> None:
    key = tree.wake_cards['sablier'].pitch_short
    tree.text[key] = replace(tree.text[key], body=tree.text[key].body + ' One more sentence, to spill a fifth row.')
    assert any('pitch_short must fit the 80x24' in m for m in messages(tree))


def test_a_full_pitch_that_does_not_fit_132x50_is_rejected(tree) -> None:
    key = tree.wake_cards['kestrel'].pitch
    tree.text[key] = replace(tree.text[key], body='\n\n'.join([tree.text[key].body] * 6))
    assert any('pitch must fit the 132x50' in m for m in messages(tree))


def test_an_unknown_short_pitch_asset_is_rejected(tree) -> None:
    tree.wake_cards['choir'] = replace(tree.wake_cards['choir'], pitch_short='faction-choir-pitch-missing')
    assert any('recruiter short pitch' in str(p) for p in validate(tree))
