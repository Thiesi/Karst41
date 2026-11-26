"""An exact, faster copy.deepcopy for the plain data on the save path (#277, D5a).

The shot path's saves and commerce commits deep-copy plain JSON-shaped
data (identities, player progress, patches) several times per commit.
`fastcopy` gives the same result as `copy.deepcopy` for trees of exact
dict, list and tuple with atomic leaves and atomic keys, and falls back to
`copy.deepcopy` of the whole value for anything else: another type, a
non-atomic key, or a container reached twice (shared or recursive), whose
sharing deepcopy preserves. tests/server/test_277_fastcopy.py checks it
against deepcopy.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, TypeVar

T = TypeVar("T")

# Types copy.deepcopy returns as the same object (copy._deepcopy_atomic), restricted
# to the ones plain data holds. Exact types only: a subclass takes the fallback.
ATOMIC = frozenset({type(None), bool, int, float, complex, str, bytes})


class _Fallback(Exception):
    """This value needs copy.deepcopy itself."""


def _walk(value: Any, seen: set[int]) -> Any:
    kind = type(value)
    if kind in ATOMIC:
        return value
    if kind is dict:
        if id(value) in seen:
            raise _Fallback
        seen.add(id(value))
        copied = {}
        for key, item in value.items():
            if type(key) not in ATOMIC:
                raise _Fallback
            copied[key] = item if type(item) in ATOMIC else _walk(item, seen)
        return copied
    if kind is list:
        if id(value) in seen:
            raise _Fallback
        seen.add(id(value))
        return [item if type(item) in ATOMIC else _walk(item, seen) for item in value]
    if kind is tuple:
        items = [_walk(item, seen) for item in value]
        if all(copied is original for copied, original in zip(items, value)):
            return value  # As deepcopy: a tuple whose items copy to themselves is itself.
        if id(value) in seen:
            raise _Fallback
        seen.add(id(value))
        return tuple(items)
    raise _Fallback


def fastcopy(value: T) -> T:
    """copy.deepcopy(value), faster for plain trees; exactly deepcopy otherwise."""
    try:
        return _walk(value, set())
    except (_Fallback, RecursionError):
        return deepcopy(value)
