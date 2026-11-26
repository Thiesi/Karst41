"""#277 (D5a): fastcopy is copy.deepcopy, faster for plain data.

- Property: for fuzzed trees of dict, list and tuple with atomic leaves,
  and for every kind of value that takes the fallback (shared and
  recursive containers, non-atomic keys, subclasses, other types),
  fastcopy(value) equals deepcopy(value), with the same types all the way
  down, the same sharing inside the copy, and the same identity to the
  source wherever deepcopy keeps it (atomics, tuples of atomics) and
  nowhere else.
- End to end: the C1 fight, with the save path's copies (characters.queue,
  skills.proposal, commerce.settle and commerce.settled_patch) on fastcopy
  and on deepcopy, gives the same dirty maps, writer commands, world and
  rows on disk.
"""

from __future__ import annotations

import random
from collections import OrderedDict, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from karst41.server import characters, commerce, skills
from karst41.server.copying import fastcopy
from tests.server.test_277_coalesce import database, run


# --- Shape comparison ----------------------------------------------------------

def containers(value: Any, found: dict[int, Any] | None = None) -> dict[int, Any]:
    """Every mutable or tuple node reachable from value, by id."""
    found = {} if found is None else found
    if isinstance(value, (dict, list, tuple, set)) and id(value) not in found:
        found[id(value)] = value
        items = (list(value.keys()) + list(value.values())) if isinstance(value, dict) else list(value)
        for item in items:
            containers(item, found)
    elif hasattr(value, "__dict__") and id(value) not in found:
        found[id(value)] = value
        containers(vars(value), found)
    return found


def same_shape(got: Any, want: Any, source: Any, path: str = "$", pairs: dict[int, int] | None = None) -> None:
    """got and want (deepcopy's result) match node for node: types, values, sharing,
    and identity with the source exactly where deepcopy keeps it."""
    pairs = {} if pairs is None else pairs
    assert type(got) is type(want), f"{path}: {type(got).__name__} != {type(want).__name__}"
    assert (got is source) == (want is source), f"{path}: identity with the source differs"
    if id(want) in pairs:  # A node deepcopy shared: fastcopy must share the same node.
        assert pairs[id(want)] == id(got), f"{path}: sharing differs"
        return
    pairs[id(want)] = id(got)
    if isinstance(want, dict):
        assert list(got.keys()) == list(want.keys()), f"{path}: keys or order differ"
        for key in want:
            same_shape(got[key], want[key], source[key], f"{path}[{key!r}]", pairs)
    elif isinstance(want, (list, tuple)):
        assert len(got) == len(want), f"{path}: length differs"
        for index, (g, w, s) in enumerate(zip(got, want, source)):
            same_shape(g, w, s, f"{path}[{index}]", pairs)
    elif hasattr(want, "__dict__"):
        same_shape(vars(got), vars(want), vars(source), f"{path}.__dict__", pairs)
    else:
        assert got == want or (got != got and want != want), f"{path}: {got!r} != {want!r}"  # NaN equals itself here.


def check(value: Any) -> None:
    got, want = fastcopy(value), deepcopy(value)
    same_shape(got, want, value)
    # Nothing mutable is shared with the source.
    mutable = {i for i, node in containers(value).items() if not isinstance(node, tuple)}
    assert not mutable & {i for i, node in containers(got).items() if not isinstance(node, tuple)}


# --- Fuzzed plain trees --------------------------------------------------------

ATOMS = [None, True, False, 0, -7, 2**70, 1.5, float("nan"), float("inf"), 2j, "", "ä", "receipt:r:1", b"\x00\xff"]


def tree(rng: random.Random, depth: int) -> Any:
    if depth <= 0 or rng.random() < 0.25:
        return rng.choice(ATOMS)
    kind = rng.choice(("dict", "list", "tuple", "tuple_atoms"))
    width = rng.randrange(0, 6)
    if kind == "dict":
        keys = [rng.choice(("a", "b", "skill_use", "receipts", "", "ä", 1, 2, 3.5, None, True))
                for _ in range(width)]
        return {key: tree(rng, depth - 1) for key in keys}
    if kind == "list":
        return [tree(rng, depth - 1) for _ in range(width)]
    if kind == "tuple_atoms":
        return tuple(rng.choice(ATOMS) for _ in range(width))
    return tuple(tree(rng, depth - 1) for _ in range(width))


def test_fuzzed_plain_trees_copy_exactly_as_deepcopy() -> None:
    rng = random.Random(277)
    for _ in range(3000):
        check(tree(rng, rng.randrange(0, 6)))


# --- The fallback: everything deepcopy treats specially ---------------------------

@dataclass
class Card:
    name: str
    tags: list[str]


class Mapping(dict):
    pass


class Number(float):
    pass


def fallback_values() -> list[Any]:
    shared = [1, 2]
    loop: list[Any] = [1]
    loop.append(loop)
    knot: dict[str, Any] = {"x": 1}
    knot["self"] = knot
    inner = {"k": [1]}
    return [
        {"a": shared, "b": shared},                      # Shared list: deepcopy keeps the sharing.
        [shared, (shared,)],                             # Shared through a tuple.
        ([1],) * 2,                                      # The same tuple twice, holding a mutable.
        loop,                                            # Recursive list.
        knot,                                            # Recursive dict.
        {"outer": [inner, inner]},                       # Shared dict.
        {(1, 2): "tuple key"},                           # A non-atomic key.
        {frozenset({1}): 1},
        Mapping(a=[1]),                                  # A dict subclass.
        OrderedDict(a=[1]),
        defaultdict(list, a=[1]),
        [Number(1.5)],                                   # A float subclass.
        {1, 2, 3},                                       # A set.
        {"when": datetime(2026, 11, 26, tzinfo=timezone.utc)},
        {"card": Card("x", ["y"])},                      # A dataclass.
        [range(3), slice(1, 2), Ellipsis, NotImplemented, len],
    ]


@pytest.mark.parametrize("index", range(len(fallback_values())))
def test_every_special_value_copies_exactly_as_deepcopy(index: int) -> None:
    value = fallback_values()[index]
    got, want = fastcopy(value), deepcopy(value)
    if index in (3, 4):  # Recursive: compare the structure by hand.
        assert type(got) is type(want)
        inner = got[1] if isinstance(got, list) else got["self"]
        assert inner is got and got is not value
        return
    same_shape(got, want, value)


def test_a_plain_value_inside_a_special_one_keeps_deepcopys_sharing() -> None:
    shared = {"receipts": ["r:1"]}
    progress = {"skill_use": shared, "copy": shared, "memory": {"seen": [1, 2]}}
    got = fastcopy(progress)
    assert got["skill_use"] is got["copy"] and got["skill_use"] is not shared
    assert got == deepcopy(progress)


def test_deep_nesting_falls_back_to_deepcopy_behaviour() -> None:
    value: Any = []
    for _ in range(50):
        value = [value, {"k": value}]
    check(value)


# --- End to end: the save path ---------------------------------------------------

@pytest.fixture(scope="module")
def content() -> Any:
    from karst41.content.loader import load_content
    tree_ = load_content(Path(__file__).parents[2] / "src/karst41/content")
    tree_.walls = None
    return tree_


def test_the_fight_writes_the_same_rows_with_fastcopy_and_with_deepcopy(tmp_path, content, monkeypatch) -> None:
    """Every changed site, on deepcopy (the reference) and on fastcopy (as shipped)."""
    calls = {"count": 0}
    real = fastcopy

    def counted(value: Any) -> Any:
        calls["count"] += 1
        return real(value)
    for module in (characters, commerce, skills):
        monkeypatch.setattr(module, "fastcopy", deepcopy)
    reference, ref_fight = run(tmp_path / "deepcopy", content, 200)
    for module in (characters, commerce, skills):
        monkeypatch.setattr(module, "fastcopy", counted)
    current, fight = run(tmp_path / "fastcopy", content, 200)
    try:
        assert calls["count"] > 100, "the fight ran the changed sites"
        assert current == reference
        assert fight.commands() == ref_fight.commands()
        assert fight.world() == ref_fight.world()
    finally:
        fight.close()
        ref_fight.close()
    assert database(tmp_path / "fastcopy" / "world.db") == database(tmp_path / "deepcopy" / "world.db")
