"""The danger hold (P8, the opening runner): speech waits after a danger line.

Terminal UI 2.5: never push danger or combat out of view. A danger or
combat line holds barks, ambience and script speech for ten seconds
(`Session.danger_at`). Runner contract amendment 3 (owner, 2026-11-15):
the hold ends early once the line's source can no longer act on the
caller. Only the caller's own training drone is tagged that way; it can
no longer act once it is down, skipped or inert. Real combat is untagged
and always holds the full ten seconds.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from karst41.server.sessions import Session

DANGER_HOLD_SECONDS = 10
TRAINING = "training:"
"""`Session.danger_source` prefix: the training drone owned by the player id that follows."""


def holds(session: Session, now: datetime, drone_can_act: Callable[[str], bool]) -> bool:
    """True while the newest danger or combat line still holds speech."""
    if session.danger_at is None:
        return False
    if (now - datetime.fromisoformat(session.danger_at)).total_seconds() >= DANGER_HOLD_SECONDS:
        return False
    source = session.danger_source or ""
    return not (source.startswith(TRAINING) and not drone_can_act(source[len(TRAINING):]))


def released(session: Session, now: datetime, drone_can_act: Callable[[str], bool]) -> str | None:
    """The stamp up to which the client's log stops protecting danger and
    combat lines (`me.danger_release`): the newest line's, while its training
    drone can no longer act and the ten seconds have not run out. None
    otherwise, so real combat keeps the full protection (terminal UI 2.5)."""
    source = session.danger_source or ""
    if session.danger_at is None or not source.startswith(TRAINING):
        return None
    if (now - datetime.fromisoformat(session.danger_at)).total_seconds() >= DANGER_HOLD_SECONDS:
        return None
    # #253: never while another source of a line at or before the stamp may
    # still act. An untagged source can't be shown to have stopped, so any
    # untagged line inside the ten seconds withholds the release.
    other = session.danger_other_at
    if other is not None and (now - datetime.fromisoformat(other)).total_seconds() < DANGER_HOLD_SECONDS:
        return None
    # The stamp is the `at` of the drone's newest line (`danger_at`), never "now".
    return None if drone_can_act(source[len(TRAINING):]) else session.danger_at
