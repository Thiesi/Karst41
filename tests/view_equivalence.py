"""#194: the original ViewStore diff, frozen, and a checker against the new one.

`reference_frames` is delta.py as it stood on main at a24d8196, verbatim but
for names. `checking()` wraps ViewStore.frames so that every call also runs
the reference on the same inputs and asserts the frames are byte-identical:
the same full frame, the same full-or-delta choice, the same ops in the same
order. Set KARST41_VIEW_EQUIVALENCE=1 to run a whole pytest session under it
(tests/conftest.py), for example the presentation goldens.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from typing import Any

from karst41.server.protocol import Frame, encode
from karst41.server.views import delta

checked = {"calls": 0, "deltas": 0}


def _ref_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def reference_changes(old: Any, new: Any, path: str = "") -> list[dict[str, Any]]:
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
                ops.extend(reference_changes(old[key], new[key], child))
    elif isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        for index, (before, after) in enumerate(zip(old, new)):
            ops.extend(reference_changes(before, after, path + f"/{index}"))
    else:
        return replacement
    return ops if not path or _ref_size(ops) < _ref_size(replacement) else replacement


def reference_frames(previous: dict[str, Any] | None, revision: int, body: dict[str, Any],
                     kind: str) -> tuple[Frame, Frame | None]:
    document = {"kind": kind, "body": body}
    full = Frame("view", {"rev": revision + 1, **document})
    delta_frame = None
    if (previous is not None and previous["kind"] == kind
            and previous["body"].get("viewport") == body.get("viewport")
            and previous["body"].get("sector") == body.get("sector")
            and previous["body"].get("scope") == body.get("scope")):
        ops = reference_changes(previous, document)
        candidate = Frame("delta", {"rev": revision + 1, "base": revision, "ops": ops})
        if len(ops) <= 256 and len(encode(candidate)) < len(encode(full)):
            delta_frame = candidate
    return full, delta_frame


def same(a: tuple[Frame, Frame | None], b: tuple[Frame, Frame | None]) -> bool:
    (full_a, delta_a), (full_b, delta_b) = a, b
    if encode(full_a) != encode(full_b) or (delta_a is None) != (delta_b is None):
        return False
    return delta_a is None or encode(delta_a) == encode(delta_b)


@contextlib.contextmanager
def checking() -> Iterator[dict[str, int]]:
    """Every ViewStore.frames call is checked against the reference, byte for byte."""
    original = delta.ViewStore.frames

    def frames(self: delta.ViewStore, session: Any, body: dict[str, Any], kind: str = "zone") -> Any:
        expected = reference_frames(self.previous, session.view_revision, body, kind)
        result = original(self, session, body, kind)
        assert same(expected, result), f"view frames differ from the reference (kind {kind!r})"
        checked["calls"] += 1
        checked["deltas"] += result[1] is not None
        return result
    delta.ViewStore.frames = frames
    reuse, delta.CHECK_REUSE = delta.CHECK_REUSE, True
    try:
        yield checked
    finally:
        delta.ViewStore.frames = original
        delta.CHECK_REUSE = reuse
