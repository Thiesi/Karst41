"""S48c2: the lesson-hint row (terminal UI 11; the presentation lead's build)."""

from karst41.door.chrome import lesson_hint
from karst41.door.cells import display_width

E_CARDS = [("E look at", "Recruiter cards")]


def test_order_is_hint_then_context_then_help() -> None:
    row = lesson_hint({"text": "Tab picks the drone.  B walks past it"}, 78, E_CARDS)
    assert row == "Tab picks the drone.  B walks past it  E look at Recruiter cards  ? help"


def test_without_context_the_row_is_the_hint_and_help() -> None:
    assert lesson_hint({"text": "Arrow keys walk. Follow the line on the floor."}, 78, []) == \
        "Arrow keys walk. Follow the line on the floor.  ? help"


def test_too_wide_drops_in_the_presentation_leads_order_and_never_cuts_the_hint() -> None:
    hint = "x" * 60  # Within the 70-cell budget.
    long_ctx = [("E go to", "Meridian Plaza Promenade")]
    row = lesson_hint({"text": hint}, 78, long_ctx)
    assert row == hint + "  ? help"  # Even the context's short form does not fit: it goes.
    shorter = "x" * 40
    assert lesson_hint({"text": shorter}, 78, long_ctx) == shorter + "  E go to Promenade  ? help"
    assert display_width(row) <= 78
    widest = "y" * 70
    row = lesson_hint({"text": widest}, 78, long_ctx)
    assert row == widest + "  ? help"
    assert lesson_hint({"text": widest + "zz"}, 78, long_ctx) == widest + "zz"  # Help goes before the hint.


def test_the_short_form_is_the_labels_last_whole_word() -> None:
    hint = "Arrow keys walk. Follow the line on the floor."
    row = lesson_hint({"text": hint}, 78, [("E talk to", "Dr. Ilse Vantongeren")])
    assert row == hint + "  E talk to Vantongeren  ? help" and display_width(row) == 77
    # A hint that leaves room for exactly the short form: the full label never fits.
    for prefix, label, short in (("E talk to", "Nurse Bea Okafor", "Okafor"), ("E open", "Wake Outfitter", "Outfitter"),
                                 ("E use", "Corridor door", "door"), ("E jack in at", "Hall terminal", "terminal")):
        group = f"{prefix} {short}"
        hint = "x" * (78 - len(group) - len("    ? help"))
        assert lesson_hint({"text": hint}, 78, [(prefix, label)]) == f"{hint}  {group}  ? help"


def test_an_errand_uses_its_short_form_before_the_context_shrinks() -> None:
    errand = {"text": "Card: Sodium Row. Follow MERIDIAN PLAZA, east.", "short": "Card: Sodium Row"}
    ctx = [("E go to", "Meridian Plaza")]
    assert lesson_hint(errand, 78, ctx) == "Card: Sodium Row. Follow MERIDIAN PLAZA, east.  E go to Meridian Plaza  ? help"
    assert lesson_hint(errand, 60, ctx) == "Card: Sodium Row  E go to Meridian Plaza  ? help"


def test_the_short_form_keeps_the_last_whole_word_before_a_trailing_parenthetical() -> None:
    """Terminal UI 11 (#253): `Public terminal (Plaza 1)` becomes `terminal`,
    never `1)`; a name with no parenthetical keeps its last word."""
    from karst41.door.chrome import short_name
    assert short_name("Public terminal (Plaza 1)") == "terminal"
    assert short_name("Dr. Ilse Vantongeren") == "Vantongeren"
    assert short_name("Hall terminal") == "terminal"
    errand = {"text": "Card: Sodium Row. Take the NEON STAIR.", "short": "Card: Sodium Row. Take the NEON STAIR."}
    row = lesson_hint(errand, 78, [("E/J jack in at", "Public terminal (Plaza 1)")])
    assert "E/J jack in at terminal" in row and "1)" not in row


def test_the_short_form_skips_digit_and_punctuation_words_and_keeps_a_label_with_none() -> None:
    """#253: `Locker 7` becomes `Locker`; a label with no word left stays whole."""
    from karst41.door.chrome import short_name
    assert short_name("Locker 7") == "Locker"
    assert short_name("Bay 12 (east)") == "Bay"
    assert short_name("7 - 12") == "7 - 12"


def test_two_short_forms_that_would_read_the_same_both_stay_whole() -> None:
    """#253: `Locker 7` and `Locker 9` would both shorten to `Locker`, so both stay whole."""
    from karst41.door.chrome import short_name
    assert short_name("Locker 7") == short_name("Locker 9") == "Locker"
    hint = "x" * 30
    ctx = [("E open", "Locker 7"), ("J open", "Locker 9")]
    row = lesson_hint({"text": hint}, 78, ctx)
    assert "E open Locker 7" in row and "J open Locker 9" in row
    # Where the whole labels cannot fit, the normal drop order applies: a bare `Locker` never shows.
    import re
    tight = lesson_hint({"text": "y" * 38}, 78, ctx)  # Short forms would fit (76), whole labels not (80).
    assert not re.search(r"Locker(?! [79])", tight), tight
