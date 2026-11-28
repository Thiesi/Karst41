"""#255: one definition of a settled screen for the NetBBS acceptance drivers.

The door sends its view ack straight to the server, but its frame bytes
reach the caller through NetBBS and Telnet. So `ack_revision ==
view_revision` can hold while the frame's last rows are still in flight, and
a capture taken then is torn: part new frame, part old (#252, c2-p5-5).

Settled therefore means both:
- acknowledged: the session's ack_revision equals its view_revision;
- whole: the caller's received stream ends with the cursor sequence that
  every rendered frame ends with (`door.chrome.cursor()`: `ESC[?25l`, or a
  cursor move then `ESC[?25h`).

This guards against a TORN frame, not a STALE one. Both conditions can hold
before any byte of the next frame has arrived, because the stream still ends
with the previous frame's cursor; a capture then is whole but old (#296's
s42 walk-away capture showed the old talk page).

#295's opt-in stricter check: `since=mark`, where the mark is taken with
`await marked(session, caller)` before the action. It also requires a whole
new frame after the mark: the stream has grown past it by at least a frame
end, and still ends with one. The mark must come from a settled stream
(#304 review 7233899866): a mark taken while a frame is in flight would let
that old frame's remaining bytes count as the new frame. Use it only after actions known to redraw. An action that renders
nothing (a key with no effect, a view the server dedupes) never grows the
stream, so it times out, with its own message saying no new frame arrived.
"""

from __future__ import annotations

import asyncio
from typing import Any

FRAME_END = (b"\x1b[?25l", b"\x1b[?25h")  # door.chrome.cursor(): the last bytes of every rendered frame.
TAIL = 40


def frame_whole(output: bytes | bytearray) -> bool:
    """The received stream ends where a rendered frame ends."""
    return bytes(output[-len(FRAME_END[0]):]) in FRAME_END


def acknowledged(session: Any) -> bool:
    return session.ack_revision == session.view_revision


def new_frame(output: bytes | bytearray, since: int) -> bool:
    """A whole frame has arrived after `since`: the stream grew past the mark
    by at least a frame end, and it ends with one."""
    return len(output) >= since + len(FRAME_END[0]) and frame_whole(output)


def settled_now(session: Any, caller: Any, since: int | None = None) -> bool:
    """For compound predicates: `settled_now(s, c) and <content check>`."""
    return (acknowledged(session) and frame_whole(caller.output)
            and (since is None or new_frame(caller.output, since)))


def unsettled(session: Any, caller: Any) -> str:
    """Which half is missing, with the stream's last bytes escaped."""
    halves = []
    if not acknowledged(session):
        halves.append(f"ack_revision {session.ack_revision} != view_revision {session.view_revision}")
    if not frame_whole(caller.output):
        halves.append("the last frame is not whole (the stream does not end with the frame's cursor sequence)")
    tail = bytes(caller.output[-TAIL:])
    return f"{'; '.join(halves) or 'settled'}; last {len(tail)} bytes: {tail!r}"


async def marked(session: Any, caller: Any, timeout: float = 10) -> int:
    """A mark for `since=`: the stream's length once the screen has settled,
    so no frame is in flight when it is taken."""
    await settled(session, caller, timeout)
    return len(caller.output)


async def settled(session: Any, caller: Any, timeout: float = 10, *, since: int | None = None) -> None:
    """Wait until the screen is settled; on timeout, say which half failed.

    With `since=mark` (from `marked()`, before an action known to redraw),
    also wait for a whole frame after the mark."""
    try:
        async with asyncio.timeout(timeout):
            while not settled_now(session, caller, since):
                await asyncio.sleep(.02)
    except TimeoutError:
        if since is not None and settled_now(session, caller):
            grew = len(caller.output) - since
            raise TimeoutError(f"no new frame arrived after {timeout}s: the stream grew {grew} bytes past the mark "
                               f"({since}); the action may not redraw. {unsettled(session, caller)}") from None
        raise TimeoutError(f"screen not settled after {timeout}s: {unsettled(session, caller)}") from None
