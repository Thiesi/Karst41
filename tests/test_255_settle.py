"""#255: a capture waits for an acknowledged view AND a whole last frame.

#295: with `since=mark`, it also waits for a whole frame after the mark.

The frame tails come from the door's own `chrome.cursor()`, so the helper's
definition of "whole" is pinned to what the door actually sends.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from karst41.door import chrome
from karst41.door.layout import compute

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from acceptance_settle import FRAME_END, frame_whole, new_frame, settled, settled_now, unsettled  # noqa: E402

BODY = '\x1b[1;1H╔══ SODIUM ROW ══╗\x1b[24;1HZone rows\x1b[0m'.encode()


@pytest.mark.parametrize('size', [(80, 24), (132, 50)])
@pytest.mark.parametrize('visible', [False, True], ids=['hidden', 'visible'])
def test_a_frame_is_whole_only_once_its_cursor_tail_has_arrived(size, visible):
    tail = chrome.cursor(compute(*size), 'Enter: talk', visible)
    assert tail.endswith(FRAME_END)  # The door's cursor tail is what the helper waits for.
    frame = BODY + tail
    assert frame_whole(frame) and frame_whole(bytearray(frame))
    for cut in range(1, len(tail) + 1):  # Mid-tail: any prefix of the cursor sequence.
        assert not frame_whole(frame[:-cut]), (visible, cut)
    assert not frame_whole(BODY[:len(BODY)//2])  # Mid-body.
    assert frame_whole(frame + BODY + tail)  # Two frames, the second whole.
    assert not frame_whole(frame + BODY)  # The next frame begun but not finished.


def caller_with(output: bytes, ack: int, view: int) -> tuple[SimpleNamespace, SimpleNamespace]:
    return SimpleNamespace(ack_revision=ack, view_revision=view), SimpleNamespace(output=bytearray(output))


def test_settled_needs_both_the_ack_and_the_whole_frame():
    whole = BODY + b'\x1b[?25l'
    assert settled_now(*caller_with(whole, 3, 3))
    assert not settled_now(*caller_with(whole, 2, 3))  # The frame arrived, its ack not yet counted.
    assert not settled_now(*caller_with(whole[:-3], 3, 3))  # Acked but torn: #252's capture.


def test_a_timeout_says_which_half_failed_and_shows_the_stream_tail():
    session, caller = caller_with(BODY + b'\x1b[24;5H\x1b[?2', 3, 3)
    with pytest.raises(TimeoutError) as error:
        asyncio.run(settled(session, caller, timeout=.05))
    message = str(error.value)
    assert 'not whole' in message and 'ack_revision' not in message
    assert repr(bytes(caller.output[-40:])) in message and '\\x1b[?2' in message
    session.ack_revision = 2
    assert 'ack_revision 2 != view_revision 3' in unsettled(session, caller)


def test_settled_returns_once_the_tail_arrives():
    session, caller = caller_with(BODY, 3, 3)

    async def run() -> None:
        waiting = asyncio.create_task(settled(session, caller, timeout=2))
        await asyncio.sleep(.05)
        assert not waiting.done()
        caller.output.extend(b'\x1b[?25l')
        await asyncio.wait_for(waiting, 1)
    asyncio.run(run())


# #295: since=mark, a whole but STALE screen is not settled.

def test_since_mark_needs_a_whole_frame_after_the_mark():
    old = BODY + b'\x1b[?25l'
    mark = len(old)
    assert settled_now(*caller_with(old, 3, 3))  # Whole and acked: #296's stale talk page passed here.
    assert not settled_now(*caller_with(old, 3, 3), since=mark)
    assert not new_frame(old + BODY, mark)  # The next frame begun but not finished.
    assert not new_frame(old + b'\x1b[?25l'[:-1], mark)
    assert new_frame(old + BODY + b'\x1b[?25h', mark)
    assert settled_now(*caller_with(old + BODY + b'\x1b[?25l', 3, 3), since=mark)
    assert not settled_now(*caller_with(old + BODY + b'\x1b[?25l', 3, 4), since=mark)  # Still needs the ack.
    assert not new_frame(old[:mark] + b'\x1b[?25l'[-3:], mark-3)  # A frame end must lie wholly after the mark.


def test_since_mark_times_out_saying_no_new_frame_arrived():
    """An action that renders nothing never grows the stream: the opt-in
    check must say so, not report a torn frame."""
    session, caller = caller_with(BODY + b'\x1b[?25l', 3, 3)
    mark = len(caller.output)
    with pytest.raises(TimeoutError) as error:
        asyncio.run(settled(session, caller, timeout=.05, since=mark))
    assert 'no new frame arrived' in str(error.value) and 'grew 0 bytes' in str(error.value)
    session, caller = caller_with(BODY + b'\x1b[?25l' + BODY, 3, 3)  # Torn: the ordinary message.
    with pytest.raises(TimeoutError) as error:
        asyncio.run(settled(session, caller, timeout=.05, since=mark))
    assert 'not settled' in str(error.value) and 'not whole' in str(error.value)


def test_since_mark_returns_once_the_new_frame_is_whole():
    session, caller = caller_with(BODY + b'\x1b[?25l', 3, 3)
    mark = len(caller.output)

    async def run() -> None:
        waiting = asyncio.create_task(settled(session, caller, timeout=2, since=mark))
        await asyncio.sleep(.05)
        caller.output.extend(BODY)
        session.view_revision = 4
        await asyncio.sleep(.05)
        assert not waiting.done()
        caller.output.extend(b'\x1b[?25l')
        session.ack_revision = 4
        await asyncio.wait_for(waiting, 1)
    asyncio.run(run())

