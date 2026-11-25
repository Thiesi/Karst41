"""#194: the cheaper ViewStore diff sends byte-identical frames (tests/view_equivalence.py)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from karst41.content.loader import load_content
from karst41.server.protocol import Frame, ProtocolError
from karst41.server.views.delta import Measure, ViewStore, _size, changes
from tests.character_helpers import create, place
from tests.harness import HeadlessServer
from tests.view_equivalence import checking, reference_changes


SAMPLES: list[Any] = [
    None, True, False, 0, -7, 2**63, 1.5, -0.0, 1e-12, 3.0, "", "plain", "ünïcödé ✓ 界", "quote \" back \\ tab \t nl \n",
    "\u0001\u001f control", [], {}, [1, [2, [3, []]]], (1, "two", 3.0), {"a": {"b": {"c": [1, 2, {"d": None}]}}},
    {1: "int key", 2.5: "float key", True: "bool key", None: "none key"}, {"~/odd": [{"x": -1}] * 3},
]


@pytest.mark.parametrize("value", SAMPLES, ids=[repr(v)[:30] for v in SAMPLES])
def test_measure_is_the_exact_compact_json_length(value: Any) -> None:
    measure = Measure()
    assert measure(value) == _size(value)
    assert measure(value) == _size(value), "a memo hit returns the same size"
    assert measure.replacement("/a~0b/1", value) == _size([{"op": "replace", "path": "/a~0b/1", "value": value}])
    assert not measure.nonfinite


def test_measure_flags_a_nan_as_encode_would_refuse_it() -> None:
    measure = Measure()
    assert measure([1, {"x": math.nan}]) == _size([1, {"x": math.nan}])
    assert measure.nonfinite
    store = ViewStore()

    class Session:
        view_revision, needs_full_view = 1, False
    store.previous = {"kind": "zone", "body": {"x": 1}}
    with pytest.raises(ProtocolError, match="invalid JSON payload"):
        store.frames(Session(), {"x": math.inf})


def test_changes_matches_the_reference_on_nested_edits() -> None:
    old = {"kind": "zone", "body": {"tiles": [[1, 2, 3], [4, 5, 6]], "actors": [{"id": "a", "x": 1}], "me": {"hp": 9}}}
    for new in (
        {"kind": "zone", "body": {"tiles": [[1, 2, 3], [4, 9, 6]], "actors": [{"id": "a", "x": 2}], "me": {"hp": 9}}},
        {"kind": "zone", "body": {"tiles": [[7, 7, 7], [7, 7, 7]], "actors": [], "me": {"hp": 8, "new": True}}},
        {"kind": "zone", "body": {"tiles": [[1, 2, 3]], "actors": [{"id": "b", "x": 1}], "extra": "x"}},
    ):
        assert json.dumps(changes(old, new)) == json.dumps(reference_changes(old, new))


@pytest.fixture(scope="module")
def content() -> Any:
    return load_content(Path(__file__).parents[2] / "src/karst41/content")


@pytest.mark.parametrize("size", [(80, 24), (132, 50)], ids=["80x24", "132x50"])
def test_a_long_walk_sends_byte_identical_frames(content: Any, size: tuple[int, int]) -> None:
    """S11-style: a caller stands among NPCs in the plaza, then walks the hubs, at both sizes."""
    width, height = size
    with checking() as counts:
        before, deltas = counts["calls"], counts["deltas"]
        game = HeadlessServer(content, character_state={})
        client = game.connect({"user_id": 1, "handle": "Walker", "node_name": "test"}, width=width, height=height)
        create(game, client, name="Walker")
        place(game, client, "core-plaza", (20, 6))
        game.frames_for(client)
        game.step(80)  # Standing: NPCs move, the view changes, the caller doesn't.
        keys = ["l", "l", "j", "j", "h", "k", "l", "j"]
        for tick in range(400):
            if tick % 2 == 0:
                game.core.receive(client.session, Frame("key", {"k": keys[(tick // 2) % len(keys)], "t": tick}))
            if tick == 200:
                place(game, client, "sodium-row", (20, 20))
            game.step()
            game.frames_for(client)
        assert counts["calls"] - before >= 120 and counts["deltas"] - deltas >= 60
