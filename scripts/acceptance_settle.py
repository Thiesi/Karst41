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
with the previous frame's cursor; a capture then is whole but old. Drivers
that need the new frame also wait for its content (#255 records a stricter,
opt-in check as follow-up).
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


def settled_now(session: Any, caller: Any) -> bool:
    """For compound predicates: `settled_now(s, c) and <content check>`."""
    return acknowledged(session) and frame_whole(caller.output)


def unsettled(session: Any, caller: Any) -> str:
    """Which half is missing, with the stream's last bytes escaped."""
    halves = []
    if not acknowledged(session):
        halves.append(f"ack_revision {session.ack_revision} != view_revision {session.view_revision}")
    if not frame_whole(caller.output):
        halves.append("the last frame is not whole (the stream does not end with the frame's cursor sequence)")
    tail = bytes(caller.output[-TAIL:])
    return f"{'; '.join(halves) or 'settled'}; last {len(tail)} bytes: {tail!r}"


async def settled(session: Any, caller: Any, timeout: float = 10) -> None:
    """Wait until the screen is settled; on timeout, say which half failed."""
    try:
        async with asyncio.timeout(timeout):
            while not settled_now(session, caller):
                await asyncio.sleep(.02)
    except TimeoutError:
        raise TimeoutError(f"screen not settled after {timeout}s: {unsettled(session, caller)}") from None
