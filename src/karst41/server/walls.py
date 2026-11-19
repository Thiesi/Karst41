"""S48d: a voice in the walls (S48 part 5; technical lead's rulings, 2026-11-19).

Rare, unattributed lines in the Wake Hall and on the Row, from `walls.json`
(the creative lead's lines). Rules:

- No speaker. A line is a `log_ambient` entry, like zone ambience, and goes
  to the triggering caller's log alone, never to anyone beside them. It
  replays like any log line. An `answer` follows as a second entry; the two
  are fitted whole as a pair at 80x24, or neither goes.
- Each line fires at most once per character. The fired ids live in
  `progress["walls"]`, written through the commerce queue. When the queue
  is full nothing is said, so a line never repeats. The list is bounded:
  only ids from the file, at most the file's size.
- At most `per_evening` lines per evening. An evening is one login: the
  count sits on the session and carries across a reconnect, as P8's
  `voice_tick` does.
- Every line passes P8's `admit()` (budget, travel, the danger hold,
  threatened), so it never pushes a danger line out of view. A refused line
  stays unfired and can come on its trigger's next occurrence. Nothing is
  queued.
- Silent while the caller's opening runner is live, and for an unread body,
  whatever the runner state (there is no character to keep "fired" on).
- Within a trigger, the first eligible line in file order fires.

The closed trigger set: `bump N` (blocked moves into terrain in a row,
counted in the input handler, reset by a move or any other input; a bump
into an actor never counts), `idle S`, `region` and `zone_entered` (within
30 s of arriving) are polled on P8's one-second stride. `jack_in`,
`jack_out` and `death_decant` fire on every occurrence and stay live for a
short window (`EVENT_SECONDS`), tried at once and then on each stride, so a
line refused at the transition itself can still come once the gate opens.

With `enabled` false the layer counts nothing and costs nothing.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable

from karst41.content import schema as s
from karst41.server.clock import TICK_HZ
from karst41.server.danger import DANGER_HOLD_SECONDS
from karst41.server.sessions import QUEUE_LIMIT, Session

NAME = "<name>"
NAME_MAX = 24         # The name prompt's max_len; the validator fits every line with a name this long.
ARRIVAL_SECONDS = 30  # zone_entered's window (technical lead, ruling 1).
DECANT_GRACE_SECONDS = 5
EVENT_SECONDS = {"jack_in": 10, "jack_out": 10, "death_decant": DANGER_HOLD_SECONDS + DECANT_GRACE_SECONDS}
"""How long a pushed occurrence stays live (ruling 2). The decant's own vat line
is a combat line and opens P8's danger hold at the decant tick, so death_decant
runs until the first stride after that hold, within five seconds (technical
lead): derived from the hold, so a change to it can't starve the trigger."""
PUSHED = frozenset({"jack_in", "jack_out", "death_decant"})


def log_fit() -> tuple[int, int, int]:
    """(guttered width, legacy width, rows) of the 80x24 log (P12)."""
    from karst41.layout import compute
    small = compute(80, 24)
    return small.log.width - 2, small.log.width, small.log.height


def rows(text: str) -> int:
    """Log rows for one entry at 80x24: the larger of the guttered log and the
    legacy log with its channel prefix (both renderers ship)."""
    from karst41.door.log import rows as legacy_rows, wrap
    guttered, legacy, _ = log_fit()
    return max(len(wrap(text, guttered, continuation=0)),
               len(legacy_rows([{"channel": "zone", "text": text}], legacy)))


def fits(text: str, answer: str = "") -> bool:
    """P12: each entry at most two rows, and a pair whole in the 80x24 log."""
    _, _, height = log_fit()
    first = rows(text)
    second = rows(answer) if answer else 0
    return first <= 2 and second <= 2 and first + second <= height


@dataclass
class Ear:
    """One connection's trigger state. Not saved: a reconnect starts it again."""

    last_input: int
    bumps: int = 0
    zone: str | None = None
    arrived: int = 0
    events: dict[str, int] = field(default_factory=dict)


class Walls:
    def __init__(self, core: Any, ambience: Any) -> None:
        self.core, self.game, self.ambience = core, core.characters, ambience
        self.commerce, self.clock = core.commerce, core.clock
        self.ears: dict[str, Ear] = {}
        self.writing: dict[str, set[str]] = {}
        self.unsaved: dict[str, set[str]] = {}
        """Ids already said whose write failed: still fired in memory, written
        again on the stride, so a failed save never repeats a line (review of
        873ba317). Bounded by the file's ids per player; emptied once written."""
        """Ids being written for a player, so a line never fires twice before its commit."""

    @property
    def voice(self) -> s.WallVoice | None:
        walls = self.game.content.walls
        return walls if walls is not None and walls.enabled and walls.lines else None

    def ear(self, session: Session, tick: int) -> Ear:
        ear = self.ears.get(session.id)
        if ear is None:
            ear = self.ears[session.id] = Ear(last_input=tick)
        return ear

    # --- pushed ---------------------------------------------------------

    def input(self, session: Session, outcome: str, tick: int) -> None:
        """Every caller input: `moved`, `bump` (into terrain), `held` (a move
        refused for another reason: cooldown, an actor) or `other`."""
        if self.voice is None:
            return  # Off: count nothing (technical lead, condition 4).
        ear = self.ear(session, tick)
        ear.last_input = tick
        if outcome == "bump":
            ear.bumps += 1
            self.attempt(session, tick)
        elif outcome != "held":
            ear.bumps = 0

    def event(self, pid: str, name: str) -> None:
        if self.voice is None:
            return
        session = next((x for x in self.core.sessions.active.values()
                        if x.player_id == pid and not x.closed and x.role == "caller"), None)
        if session is None:
            return
        tick = self.clock.now_tick()
        self.ear(session, tick).events[name] = tick
        self.attempt(session, tick)

    # --- polled, on P8's stride ------------------------------------------

    def stride(self, tick: int, sessions: Iterable[Session]) -> None:
        if self.voice is None:
            return  # Off: no per-caller work at all (condition 5).
        for session in sessions:
            if session.closed or session.role != "caller":
                continue
            avatar = self.game.sim.avatars.get(session.user_id)
            if avatar is None or not avatar.actor.zone:
                continue
            self.retry(session.player_id)
            ear = self.ear(session, tick)
            if ear.zone != avatar.actor.zone:
                ear.zone, ear.arrived = avatar.actor.zone, tick
            for name, at in tuple(ear.events.items()):
                if tick - at > EVENT_SECONDS[name] * TICK_HZ:
                    del ear.events[name]
            self.attempt(session, tick)
        live = {sid for sid, x in self.core.sessions.active.items() if not x.closed}
        for sid in set(self.ears) - live:
            del self.ears[sid]  # Bounded: only open connections keep trigger state.

    # --- firing ----------------------------------------------------------

    def fired(self, player: Any, lid: str) -> bool:
        return (lid in player.progress.get("walls", ()) or lid in self.writing.get(player.id, ())
                or lid in self.unsaved.get(player.id, ()))

    def holds(self, line: s.WallLine, ear: Ear, actor: Any, tick: int) -> bool:
        trigger = line.trigger
        if trigger.name == "bump":
            return ear.bumps >= trigger.count
        if trigger.name == "idle":
            return tick - ear.last_input >= trigger.count * TICK_HZ
        if trigger.name == "region":
            x, y = actor.position
            return any(rx <= x < rx + w and ry <= y < ry + h for rx, ry, w, h in trigger.rects)
        if trigger.name == "zone_entered":
            return ear.zone == actor.zone and tick - ear.arrived < ARRIVAL_SECONDS * TICK_HZ
        return trigger.name in ear.events

    def due(self, player: Any, ear: Ear, actor: Any, tick: int) -> s.WallLine | None:
        """The first eligible line in file order whose trigger holds."""
        voice = self.voice
        assert voice is not None
        for line in voice.lines:
            if line.zones and actor.zone not in line.zones:
                continue
            if self.fired(player, line.id) or not self.holds(line, ear, actor, tick):
                continue
            return line
        return None

    def attempt(self, session: Session, tick: int) -> bool:
        voice = self.voice
        if voice is None or session.walls_heard >= voice.per_evening:
            return False
        pid = session.player_id
        player = self.game.players.get(pid) if pid else None
        if player is None or self.game.unread(pid):
            return False  # No character to keep "fired" on (ruling (b)).
        if self.game.opening_live(pid):
            return False  # Silent during the opening (ruling (b)).
        avatar = self.game.sim.avatars.get(session.user_id)
        if avatar is None or not avatar.actor.zone:
            return False
        ear = self.ear(session, tick)
        line = self.due(player, ear, avatar.actor, tick)
        if line is None:
            return False
        text = line.text.replace(NAME, player.name)
        if not fits(text, line.answer):
            return False  # Neither entry goes; the line stays unfired.
        if self.commerce.pending and len(self.commerce.waiting) >= QUEUE_LIMIT:
            return False  # Full queue: nothing said, so nothing can repeat (condition 3).
        if not self.ambience.admit(session, tick):
            return False  # Refused: unfired, for the trigger's next occurrence.
        if not self.persist(player, line.id):
            return False
        at = self.clock.now_utc().isoformat()
        for entry in (text, line.answer):
            if entry:
                session.emit(entry, kind="ambient", channel="zone", role="log_ambient", at=at)
        session.walls_heard += 1
        if line.trigger.name == "bump":
            ear.bumps = 0
        ear.events.pop(line.trigger.name, None)
        return True

    def persist(self, player: Any, lid: str) -> bool:
        voice = self.voice
        assert voice is not None
        writing = self.writing.setdefault(player.id, set())
        writing.add(lid)

        def failed() -> None:
            writing.discard(lid)
            self.unsaved.setdefault(player.id, set()).add(lid)  # Said already: keep it fired, write again.

        def done(success: bool) -> None:
            if success:
                writing.discard(lid)
            else:
                failed()

        def write() -> None:
            progress = deepcopy(player.progress)
            heard = set(progress.get("walls", ())) | {lid}
            progress["walls"] = [ln.id for ln in voice.lines if ln.id in heard]  # Bounded by the file.
            if not self.commerce.settle(player, {"progress": progress}, self.commerce.items,
                                        self.commerce.caches, self.commerce.serial, done):
                failed()
        try:
            self.commerce.enqueue_system(write)
        except ValueError:
            writing.discard(lid)
            return False
        return True

    def retry(self, pid: str | None) -> None:
        """Write again the ids whose save failed (see `unsaved`)."""
        if not pid or not self.unsaved.get(pid) or self.writing.get(pid):
            return
        player = self.game.players.get(pid)
        if player is None:
            return
        for lid in sorted(self.unsaved.pop(pid)):
            if lid in player.progress.get("walls", ()):
                continue
            if not self.persist(player, lid):
                self.unsaved.setdefault(pid, set()).add(lid)  # Queue full: next stride.
