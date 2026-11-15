"""S48c3a / #249: in a Lattice core, movement wins over B.

A key the caller's keymap resolves to `move_*` moves, never cancels or
leaves. Esc still cancels, `menu_close` and `lat.leave` still leave, and a
raw B that is not a move leaves in every keymap.
"""

import json
from pathlib import Path

import pytest

from karst41.server.protocol import Frame
from tests.character_helpers import act
from tests.lattice.test_encounters import enter
from tests.lattice.test_engine import intent, runners  # noqa: F401

LEDGER = "lat-civic-ledger"  # r-entry (0,0): r-hall-panel (0,1) is its only down-left neighbour.


def key(game, client, keymap: str, k: str) -> None:
    client.session.keymap_id = keymap
    act(game, client, Frame("key", {"k": k, "t": 0}))


def test_vi_b_moves_down_left_inside_the_core(runners) -> None:
    game, a, _, _ = runners
    presence = enter(game, a, LEDGER)
    assert presence.room == "r-entry"
    key(game, a, "vi", "b")
    assert presence.core == LEDGER and presence.room_destination == "r-hall-panel"


def test_vi_b_does_not_cancel_a_pending_move(runners) -> None:
    game, a, _, _ = runners
    presence = enter(game, a, LEDGER)
    intent(game, a, "lat.room", room="r-foyer")
    assert presence.room_destination == "r-foyer"
    key(game, a, "vi", "b")
    assert presence.core == LEDGER and presence.room_destination == "r-foyer"


@pytest.mark.parametrize(("keymap", "k"), [("vi", "B"), ("arrows", "B"), ("arrows", "b")])
def test_b_that_is_not_a_move_leaves_the_core(runners, keymap: str, k: str) -> None:
    game, a, _, _ = runners
    presence = enter(game, a, LEDGER)
    key(game, a, keymap, k)
    assert presence.core is None and presence.instance is None


@pytest.mark.parametrize(("keymap", "k"), [("vi", "B"), ("arrows", "B"), ("arrows", "b")])
def test_b_that_is_not_a_move_cancels_a_pending_move(runners, keymap: str, k: str) -> None:
    game, a, _, _ = runners
    presence = enter(game, a, LEDGER)
    intent(game, a, "lat.room", room="r-foyer")
    key(game, a, keymap, k)
    assert presence.core == LEDGER and presence.room_destination is None


def test_vi_escape_cancels_and_menu_close_leaves(runners) -> None:
    game, a, _, _ = runners
    presence = enter(game, a, LEDGER)
    intent(game, a, "lat.room", room="r-foyer")
    key(game, a, "vi", "escape")
    assert presence.core == LEDGER and presence.room_destination is None
    a.session.keymap_id = "vi"
    intent(game, a, "menu_close")
    assert presence.core is None


def test_hint_rows_literal_b_is_never_a_move_in_any_keymap(repo_root: Path) -> None:
    """The rows' literal "B" (beats 6 and 8) names a key that skips or leaves.

    That holds only while no keymap binds B to a `move_*` action; vi binds it
    to sprint_down_left, which neither the core nor the drone lesson treats
    as a move. The behaviour is pinned above and in test_s48c3a_drone_back.
    """
    content = repo_root/"src/karst41/content"
    script = json.loads((content/"scripts/wake-opening.json").read_text(encoding="utf-8"))
    texts = []
    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("text"), str):
                texts.append(node["text"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(script)
    literal = [t for t in texts if "B walks past it" in t or "B leaves" in t or t.startswith("B takes you")]
    assert len(literal) == 6  # 6.x twice, 8.x three times, and 8's way back up.
    keymaps = json.loads((content/"keymaps.json").read_text(encoding="utf-8"))["keymaps"]
    assert {table["id"] for table in keymaps} == {"arrows", "vi"}
    for table in keymaps:
        moves = {k for action, keys in table["bindings"].items() if action.startswith("move_") for k in keys}
        assert "B" not in moves, table["id"]
