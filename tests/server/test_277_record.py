"""#277 (C2): Player.record() without dataclasses.asdict, value for value.

`players.plain` must convert exactly as asdict does: nested dataclasses
become dicts, namedtuples and list, tuple and dict subclasses keep their
type, and anything else is deep-copied. Equal values are not enough (a
tuple equals no list, but 1 == 1.0 == True), so every comparison here also
checks types, recursively.
"""

from __future__ import annotations

import datetime
from collections import OrderedDict, defaultdict, namedtuple
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from random import Random
from typing import Any

import pytest

from karst41.content.loader import load_content
from karst41.server.players import plain
from karst41.server.rules import character as rules
from tests.server.test_277_coalesce import Fight

Pair = namedtuple("Pair", "left right")


@dataclass
class Inner:
    name: str = "inner"
    values: list[Any] = field(default_factory=list)
    pair: tuple[int, int] = (1, 2)


@dataclass
class Outer:
    inner: Inner = field(default_factory=Inner)
    table: dict[str, Any] = field(default_factory=dict)


@dataclass
class Box:
    value: Any


class Opaque:
    """A non-atomic value asdict deep-copies."""

    def __init__(self, n: int) -> None:
        self.n = n

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Opaque) and other.n == self.n

    def __hash__(self) -> int:
        return hash(self.n)


def same(want: Any, got: Any, path: str = "value") -> None:
    """Equal and of the same type, all the way down, with no shared mutable parts."""
    assert type(got) is type(want), f"{path}: {type(got).__name__} != {type(want).__name__}"
    if isinstance(want, dict):
        assert list(got) == list(want), f"{path}: keys or key order differ"
        for key in want:
            same(want[key], got[key], f"{path}[{key!r}]")
        if isinstance(want, defaultdict):
            assert got.default_factory is want.default_factory, f"{path}: default_factory differs"
    elif isinstance(want, (list, tuple)):
        assert len(got) == len(want), f"{path}: length differs"
        for index, (a, b) in enumerate(zip(want, got)):
            same(a, b, f"{path}[{index}]")
    else:
        assert got == want, f"{path}: {got!r} != {want!r}"


def reference(value: Any) -> Any:
    return asdict(Box(value))["value"]


def fuzz(rng: Random, depth: int = 0) -> Any:
    leaves = [lambda: rng.randint(-5, 5), lambda: rng.random(), lambda: rng.choice(["", "a", "Ω", "slv"]),
              lambda: None, lambda: rng.random() < .5, lambda: complex(1, 2), lambda: b"bytes",
              lambda: Decimal("1.5"), lambda: datetime.date(2026, 11, 26), lambda: Opaque(rng.randint(0, 3)),
              lambda: frozenset({1, 2}), lambda: {1, 2, 3}]
    if depth >= 4 or rng.random() < .3:
        return rng.choice(leaves)()
    kinds = [
        lambda: {rng.choice(["a", "b", "c", 1, (1, 2), Pair(1, 2)]): fuzz(rng, depth + 1) for _ in range(rng.randint(0, 4))},
        lambda: [fuzz(rng, depth + 1) for _ in range(rng.randint(0, 4))],
        lambda: tuple(fuzz(rng, depth + 1) for _ in range(rng.randint(0, 3))),
        lambda: Pair(fuzz(rng, depth + 1), fuzz(rng, depth + 1)),
        lambda: OrderedDict((str(i), fuzz(rng, depth + 1)) for i in range(rng.randint(0, 3))),
        lambda: defaultdict(list, {str(i): fuzz(rng, depth + 1) for i in range(rng.randint(0, 3))}),
        lambda: Inner(rng.choice(["x", "y"]), [fuzz(rng, depth + 1)], (rng.randint(0, 9), rng.randint(0, 9))),
        lambda: Outer(Inner(values=[fuzz(rng, depth + 1)]), {"k": fuzz(rng, depth + 1)}),
    ]
    return rng.choice(kinds)()


def test_plain_converts_exactly_as_asdict_on_fuzzed_values() -> None:
    rng = Random(277)
    for case in range(3000):
        value = fuzz(rng)
        same(reference(value), plain(value), f"case {case}")


def test_plain_turns_nested_dataclasses_into_dicts_and_copies_the_rest() -> None:
    nested = Outer(Inner("n", [Inner("deep", [{1, 2}]), Opaque(3)], (4, 5)), {"set": {9}, "pair": Pair([1], (2,))})
    got = plain(nested)
    same(reference(nested), got)
    assert got["inner"]["values"][0] == {"name": "deep", "values": [{1, 2}], "pair": (1, 2)}
    assert isinstance(got["table"]["pair"], Pair)
    # Deep copies: nothing mutable is shared with the original (the deepcopy fallback included).
    nested.inner.values[0].values[0].add(3)
    nested.table["set"].add(10)
    nested.inner.values[1].n = 99
    nested.table["pair"].left.append(2)
    assert got["inner"]["values"][0]["values"][0] == {1, 2}
    assert got["table"]["set"] == {9}
    assert got["inner"]["values"][1] == Opaque(3)
    assert got["table"]["pair"].left == [1]


@pytest.fixture(scope="module")
def content() -> Any:
    tree = load_content(Path(__file__).parents[2] / "src/karst41/content")
    tree.walls = None
    return tree


def test_player_record_matches_asdict_for_every_player_through_a_fight(tmp_path, content) -> None:
    """Real players, as the fight changes them: health, progress ledgers, cooldown stamps."""
    fight = Fight(tmp_path, content)
    try:
        players = fight.game.core.characters.players
        checked = 0
        for tick in range(240):
            fight.tick()
            if tick % 8:
                continue
            for player in players.values():
                want = {**asdict(player), "name_key": rules.name_key(player.name)}
                got = player.record()
                same(want, got, f"tick {tick} player record")
                got["progress"]["probe"] = True  # The record is the caller's own copy.
                assert "probe" not in player.progress
                checked += 1
        assert checked >= 30
        assert any(player.progress for player in players.values()), "the fight never filled a progress ledger"
    finally:
        fight.close()
