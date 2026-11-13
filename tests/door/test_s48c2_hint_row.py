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
    assert lesson_hint({"text": shorter}, 78, long_ctx) == shorter + "  E go to Meridian...  ? help"
    assert display_width(row) <= 78
    widest = "y" * 70
    row = lesson_hint({"text": widest}, 78, long_ctx)
    assert row == widest + "  ? help"
    assert lesson_hint({"text": widest + "zz"}, 78, long_ctx) == widest + "zz"  # Help goes before the hint.


def test_an_errand_uses_its_short_form_before_the_context_shrinks() -> None:
    errand = {"text": "Card: Sodium Row. Follow MERIDIAN PLAZA, east.", "short": "Card: Sodium Row"}
    ctx = [("E go to", "Meridian Plaza")]
    assert lesson_hint(errand, 78, ctx) == "Card: Sodium Row. Follow MERIDIAN PLAZA, east.  E go to Meridian Plaza  ? help"
    assert lesson_hint(errand, 60, ctx) == "Card: Sodium Row  E go to Meridian Plaza  ? help"
