"""Revisioned JSON-pointer patches compatible with the S04 client.

#194 (S11): the choices are unchanged; only their cost is. `changes()` used
to json.dumps both candidate encodings at every node on the way down, so a
large subtree was serialised again at every level. `ViewStore` then
encoded both frames only to compare their lengths. Now every size is the
exact byte length json.dumps would produce, computed once per value per
call (`Measure`) and summed. The frames on the wire, the full-or-delta
choice and the ops and their order are byte-identical
(tests/server/test_view_delta_equivalence.py).
"""

import json
import zlib
from typing import Any

from karst41.server.protocol import MAX_FRAME, Frame, ProtocolError, validate
from karst41.server.sessions import Session

_COMPACT = {"ensure_ascii": False, "separators": (",", ":")}

# The carried sizes rely on the zone view replacing its grids, never editing them in
# place (zone_view.build). The test suite and the equivalence runs set this, so a
# grid reused by identity with different content fails loudly instead of sending a
# wrong delta.
CHECK_REUSE = False


def _size(value: object) -> int:
    """The reference: the byte length of the compact encoding."""
    return len(json.dumps(value, **_COMPACT).encode("utf-8"))


class Measure:
    """Byte lengths of compact JSON encodings, each value measured once per call.

    A container's size is its brackets and commas plus its children's sizes.
    A child already measured (a changed subtree, measured on the way up)
    comes from the memo; any other child is measured by one C json.dumps,
    never by recursing into it. So an unchanged subtree is serialised once,
    and only the changed spine is summed here. Memoised by object identity;
    each entry keeps its object, so no id is reused while the memo lives (one
    changes() or publish call).
    """

    __slots__ = ("memo", "nonfinite")

    def __init__(self) -> None:
        self.memo: dict[int, tuple[object, int]] = {}
        self.nonfinite = False  # A NaN or infinity, which encode() refuses (allow_nan=False).

    def __call__(self, value: object) -> int:
        hit = self.memo.get(id(value))
        if hit is not None and hit[0] is value:
            return hit[1]
        if isinstance(value, dict):
            size = 2 + max(0, len(value) - 1)
            for key, item in value.items():
                if isinstance(key, str):
                    size += self._dump(key)
                else:  # json converts the key to text; measure it the same way.
                    size += len(json.dumps({key: None}, **_COMPACT).encode("utf-8")) - 7
                size += 1 + self._child(item)
        elif isinstance(value, (list, tuple)):
            size = 2 + max(0, len(value) - 1)
            for item in value:
                size += self._child(item)
        else:
            size = self._dump(value)
        self.memo[id(value)] = (value, size)
        return size

    def replacement(self, path: str, new: object) -> int:
        """The size of [{"op":"replace","path":path,"value":new}] without building it.

        The node itself is measured one level deep, so its changed children come
        from the memo, its unchanged ones are serialised once and memoised, and
        its own size is memoised for the level above and for the full frame.
        """
        return _REPLACE_FIXED + self._dump(path) + self(new)

    def _child(self, item: object) -> int:
        hit = self.memo.get(id(item))
        if hit is not None and hit[0] is item:
            return hit[1]
        size = self._dump(item)
        self.memo[id(item)] = (item, size)
        return size

    def _dump(self, value: object) -> int:
        try:
            text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except ValueError:
            self.nonfinite = True  # Measured as _size does; encode() would refuse it.
            text = json.dumps(value, **_COMPACT)
        return len(text.encode("utf-8"))


# len('[{"op":"replace","path":' + ',"value":' + '}]'), the fixed bytes of a one-op replacement.
_REPLACE_FIXED = _size([{"op": "replace", "path": "", "value": 0}]) - _size("") - _size(0)


def changes(old: Any, new: Any, path: str = "", measure: Measure | None = None) -> list[dict[str, Any]]:
    return _changes(old, new, path, measure if measure is not None else Measure())


def _changes(old: Any, new: Any, path: str, measure: Measure) -> list[dict[str, Any]]:
    if old == new:
        return []
    replacement = [{"op": "replace", "path": path, "value": new}]
    ops: list[dict[str, Any]] = []
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(old.keys() | new.keys()):
            child = path + "/" + str(key).replace("~", "~0").replace("/", "~1")
            if key not in new:
                ops.append({"op": "remove", "path": child})
            elif key not in old:
                ops.append({"op": "add", "path": child, "value": new[key]})
            else:
                ops.extend(_changes(old[key], new[key], child, measure))
    elif isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        for index, (before, after) in enumerate(zip(old, new)):
            ops.extend(_changes(before, after, path + f"/{index}", measure))
    else:
        return replacement
    # Sparse cell/actor updates usually win; large row edits replace the row.
    return ops if not path or measure(ops) < measure.replacement(path, new) else replacement


def _payload(frame: Frame, measure: Measure) -> int:
    """len() of encode(frame)'s JSON payload, with encode()'s checks."""
    validate(frame.type, frame.data)
    measure.nonfinite = False
    size = measure(frame.object())
    if measure.nonfinite:
        raise ProtocolError("invalid JSON payload")
    if not 0 < size <= MAX_FRAME:
        raise ProtocolError("frame exceeds 256 KiB")
    return size


def _digest(value: object) -> int:
    return zlib.crc32(json.dumps(value, **_COMPACT).encode("utf-8"))


class ViewStore:
    def __init__(self) -> None:
        self.previous: dict[str, Any] | None = None
        # Sizes of the previous body's top-level values (the tile grid, memory,
        # legend, charted and look grids, which the zone view reuses by identity
        # until they change). They stay valid for the same reason the diff does:
        # a published document is never modified afterwards.
        self.carry: dict[int, tuple[object, int]] = {}
        self.measure: Measure | None = None
        self.digests: dict[int, tuple[object, int]] = {}  # CHECK_REUSE only.

    def _check_reuse(self, body: dict[str, Any]) -> None:
        for key, value in body.items():
            seen = self.digests.get(id(value))
            if seen is not None and seen[0] is value and _digest(value) != seen[1]:
                raise AssertionError(f"view value {key!r} was edited in place after it was published")

    def frames(self, session: Session, body: dict[str, Any], kind: str = "zone") -> tuple[Frame, Frame | None]:
        """The full frame, and the delta when it's smaller (no side effects)."""
        document = {"kind": kind, "body": body}
        revision = session.view_revision + 1
        full = Frame("view", {"rev": revision, **document})
        delta = None
        if (self.previous is not None and self.previous["kind"] == kind
                and self.previous["body"].get("viewport") == body.get("viewport")
                and self.previous["body"].get("sector") == body.get("sector")
                and self.previous["body"].get("scope") == body.get("scope")):
            measure = Measure()
            measure.memo.update(self.carry)
            self.measure = measure
            ops = _changes(self.previous, document, "", measure)
            candidate = Frame("delta", {"rev": revision, "base": session.view_revision, "ops": ops})
            if len(ops) <= 256 and _payload(candidate, measure) < _payload(full, measure):
                delta = candidate
        return full, delta

    def publish(self, session: Session, body: dict[str, Any], tick: int,
                kind: str = "zone") -> bool:
        """Take ownership of a snapshot whose nested arrays stay immutable."""
        document = {"kind": kind, "body": body}
        if CHECK_REUSE:
            self._check_reuse(body)
        if document == self.previous and not session.needs_full_view:
            return False
        self.measure = None
        full, delta = self.frames(session, body, kind)
        if session.publish_view(full, delta, tick):
            self.previous = {"kind": kind, "body": body}
            memo = self.measure.memo if self.measure is not None else {}
            self.carry = {id(value): memo[id(value)] for value in body.values()
                          if id(value) in memo and memo[id(value)][0] is value}
            if CHECK_REUSE:
                self.digests = {id(value): (value, _digest(value)) for value in body.values()
                                if isinstance(value, (list, dict))}
            self.measure = None
            return True
        self.measure = None
        return False
