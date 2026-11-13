"""S48c2: the opening's beats runner (#220 section 4; technical lead's contract).

One runner state per caller, for the opening only. It starts when a caller
is put in the Wake Hall with an unread body, and ends when Vesper's talk
page first opens, or when the caller detaches after the record has been
read and the hall left (only the errand, a per-character flag, goes on).

- Speech (Vantongeren, Okafor) is queued and paced: at least four seconds
  between two lines to this caller, from any source (`session.voice_tick`
  is shared with P8). Every other kind is said at once and never queued.
- When a beat begins, its own lines go first; waiting non-anchor lines of
  earlier beats are dropped. An older anchor is spoken only when no line
  of the current beat is waiting, oldest first.
- Quiet for speech: a target set, an ICE warned, a menu or text page open,
  jacked in, or within P8's ten-second danger hold. `fight` lines may
  speak during the drone fight; `record` lines while the record or the
  name prompt is open.
- Leaving the hall drops every waiting line: nothing from the hall plays
  outside it.
- P8's `admit()` is bypassed; each script line sets `session.voice_tick`,
  so barks and ambience stay quiet behind it, and S48d's wall lines (which
  go through `admit()`) are quiet during the opening by construction.
- Everything goes to the caller's session alone, and into the replay ring.
- Polling (position, steps, seconds, target) runs only while the current
  beat waits on a polled trigger; otherwise the runner is purely pushed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from karst41.content import schema as s
from karst41.hints import resolve_keys
from karst41.server.clock import TICK_HZ
from karst41.server.loop import TickContext
from karst41.server.sessions import Session

GAP_SECONDS = 4
DANGER_HOLD_SECONDS = 10  # Shared with P8 (terminal UI 2.5).
SCRIPT_ID = "wake-opening"


def fresh() -> dict[str, Any]:
    """A runner state, as stored in the wake record and later in `progress`."""
    return {"beat": 0, "spoken": [], "fired": [], "queue": [], "facts": [], "hints": [],
            "beat_tick": None, "steps": 0, "pos": None, "read": False, "done": False}


class Opening:
    def __init__(self, core: Any) -> None:
        self.core, self.game = core, core.characters
        self.script: s.Script | None = self.game.content.scripts.get(SCRIPT_ID)
        self.states: dict[str, dict[str, Any]] = {}
        self.lines: dict[str, tuple[int, s.ScriptLine]] = {}
        self.after: dict[str, list[str]] = {}
        if self.script:
            for index, beat in enumerate(self.script.beats):
                for ln in beat.lines:
                    self.lines[ln.id] = index, ln
                    if ln.after:
                        self.after.setdefault(ln.after, []).append(ln.id)
        hook = self.game.lesson_hook
        self.game.lesson_hook = lambda player, key, value: (hook(player, key, value), self.push_player(player, key, value))

    # --- lifetime -------------------------------------------------------

    def start(self, session: Session, state: dict[str, Any]) -> None:
        """Attach a caller's stored state (fresh or resumed)."""
        if not self.script or state.get("done"):
            return
        if state.get("queue"):  # Resume: waiting non-anchors are dropped (technical lead, ruling b).
            state["queue"] = [q for q in state["queue"] if self.lines[q[0]][1].anchor]
        if state.get("beat_tick") is None:
            state["beat_tick"] = self.core.clock.now_tick()
            self.begin(session, state, state.get("beat", 0))
        state["pos"] = None
        self.states[session.id] = state

    def stop(self, session: Session) -> dict[str, Any] | None:
        return self.states.pop(session.id, None)

    def live(self, pid: str) -> bool:
        """A live script owns the opening's events for this player (technical lead, fix 1)."""
        return any(x.player_id == pid and not self.states[x.id].get("done")
                   for x in self.core.sessions.active.values() if x.id in self.states)

    def finish(self, state: dict[str, Any]) -> None:
        state["done"] = True
        state["queue"] = []

    # --- triggers -------------------------------------------------------

    def push_player(self, player: Any, key: str, value: Any) -> None:
        session = next((x for x in self.core.sessions.active.values()
                        if x.player_id == player.id and not x.closed and x.id in self.states), None)
        if session is not None:
            self.push(session, key, value)

    def push(self, session: Session, name: str, value: Any = True) -> None:
        state = self.states.get(session.id)
        if state is None or state.get("done") or not self.script:
            return
        name = {"drone": "drone_down" if value == "done" else "drone_skipped"}.get(name, name)
        if name in s.LESSON_FACTS and name not in state["facts"]:
            state["facts"].append(name)
        if name == "template_read" or name == "kit_issued":
            state["read"] = True
        self.fire(session, state, name, value)
        if name == "talk_opened" and value == "vesper":
            self.finish(state)
            player = self.game.players.get(session.player_id)
            if player is not None:
                player.progress.pop("errand", None)  # The errand clears on Vesper's page (#220 beat 10).
        if name == "kit_issued":
            player = self.game.players.get(session.player_id)
            if player is not None:
                player.progress["errand"] = True  # A per-character flag, not runner state.

    def matches(self, trigger: s.ScriptTrigger, name: str, value: Any) -> bool:
        if trigger.name != name:
            return False
        return not trigger.value or str(value) == trigger.value

    def fire(self, session: Session, state: dict[str, Any], name: str, value: Any) -> None:
        beats = self.script.beats
        # A triggered line, in any beat (the record can open from beat 3).
        for lid, (index, ln) in self.lines.items():
            if lid in state["fired"] or not any(self.matches(t, name, value) for t in ln.when):
                continue
            if not self.eligible(state, ln):
                state["fired"].append(lid)
                continue
            state["fired"].append(lid)
            if index > state["beat"]:
                self.begin(session, state, index)
            self.queue_line(session, state, lid, front=True)
        # The current beat ends.
        beat = beats[state["beat"]] if state["beat"] < len(beats) else None
        if beat and any(self.matches(t, name, value) for t in beat.next) and state["beat"] + 1 < len(beats):
            self.begin(session, state, state["beat"] + 1)

    def eligible(self, state: dict[str, Any], ln: s.ScriptLine) -> bool:
        facts = set(state["facts"])
        return all(f in facts for f in ln.requires) and not any(f in facts for f in ln.unless)

    def begin(self, session: Session, state: dict[str, Any], index: int) -> None:
        state["beat"], state["beat_tick"], state["steps"] = index, self.core.clock.now_tick(), 0
        state["queue"] = [q for q in state["queue"] if q[1] >= index or self.lines[q[0]][1].anchor]
        for ln in self.script.beats[index].lines:
            if not ln.when and not ln.after and ln.id not in state["fired"]:
                state["fired"].append(ln.id)
                if self.eligible(state, ln):
                    self.queue_line(session, state, ln.id)

    def queue_line(self, session: Session, state: dict[str, Any], lid: str, front: bool = False,
                   due: int = 0) -> None:
        index, ln = self.lines[lid]
        if ln.kind != "speech":
            self.say(session, state, ln)  # Event lines come at once (creative lead, fix 1).
            return
        entry = [lid, index, due]
        if front:  # Ahead of older anchors, behind the current beat's waiting lines.
            at = next((i for i, q in enumerate(state["queue"]) if q[1] < state["beat"]), len(state["queue"]))
            state["queue"].insert(at, entry)
        else:
            state["queue"].append(entry)

    # --- speaking -------------------------------------------------------

    def say(self, session: Session, state: dict[str, Any], ln: s.ScriptLine) -> None:
        at = self.core.clock.now_utc().isoformat()
        if ln.kind == "speech":
            name = ln.name or self.script.speakers.get(ln.speaker) or self.game.content.dialogue[ln.speaker].name
            session.emit(f"{name}: {ln.text}", kind="speech", channel="zone", role="chan_zone", at=at,
                         speaker=name, speaker_role="service")
            session.voice_tick = self.core.clock.now_tick()  # P8 stays quiet behind it.
        else:
            role = {"danger": "warning", "reward": "chan_sys", "combat": "chan_sys"}.get(ln.kind, "chan_sys")
            channel = "combat" if ln.kind in ("danger", "combat") else "system"
            session.emit(ln.text, kind=ln.kind, channel=channel, role=role, at=at)
            if ln.kind == "danger":
                session.danger_at = at
        state["spoken"].append(ln.id)
        for follower in self.after.get(ln.id, ()):
            _, nxt = self.lines[follower]
            if follower not in state["fired"]:
                state["fired"].append(follower)
                if self.eligible(state, nxt):
                    self.queue_line(session, state, follower,
                                    due=self.core.clock.now_tick() + nxt.delay * TICK_HZ)

    def quiet(self, session: Session, ln: s.ScriptLine, tick: int) -> bool:
        """Speech only (creative lead, fix 1)."""
        if session.voice_tick is not None and tick - session.voice_tick < GAP_SECONDS * TICK_HZ:
            return True
        avatar = self.game.sim.avatars.get(session.user_id)
        actor = avatar.actor if avatar else None
        fight = ln.fight and any(a.owner == session.player_id and a.behaviour.get("training")
                                 for a in self.core.world.actors.values())
        if not fight and session.danger_at is not None and (
                self.core.clock.now_utc() - datetime.fromisoformat(session.danger_at)).total_seconds() < DANGER_HOLD_SECONDS:
            return True
        if actor is not None and actor.target and not fight:
            return True
        if actor is not None and actor.stance == "jacked":
            return True
        wake = self.game.wakes.get(session.id)
        record_open = wake is not None and wake.stage in ("record", "name")
        menu = (self.game.menu_open(session) or session.id in self.game.screens
                or session.id in self.game.sim.external_views or session.id in self.game.sim.maps
                or (self.core.dialogue is not None and session.id in self.core.dialogue.menus)
                or session.id in self.core.commerce.menus)
        if menu and not (ln.record and record_open):
            return True
        if not ln.record and record_open:
            return True
        return False

    # --- the tick -------------------------------------------------------

    def tick(self, ctx: TickContext) -> None:
        if not self.states or not self.script:
            return
        tick = ctx.tick
        for sid, state in tuple(self.states.items()):
            session = self.core.sessions.active.get(sid)
            if session is None or session.closed or state.get("done"):
                self.states.pop(sid, None)
                continue
            avatar = self.game.sim.avatars.get(session.user_id)
            if avatar is None:
                continue
            actor = avatar.actor
            in_hall = actor.zone == self.script.zone
            if not in_hall and state["queue"]:
                state["queue"] = []  # Nothing from the hall plays outside it (creative lead, fix 3).
            self.poll(session, state, actor, in_hall, tick)
            if state["queue"] and in_hall:
                self.speak_next(session, state, tick)

    def polled(self, state: dict[str, Any]) -> set[str]:
        beat = self.script.beats[state["beat"]]
        names = {t.name for t in beat.next if t.name in s.SCRIPT_POLLED}
        names |= {t.name for ln in beat.lines if ln.id not in state["fired"]
                  for t in ln.when if t.name in s.SCRIPT_POLLED}
        return names

    def poll(self, session: Session, state: dict[str, Any], actor: Any, in_hall: bool, tick: int) -> None:
        names = self.polled(state)
        if not names:
            return  # Purely pushed: no per-tick cost (technical lead, fix 2).
        pos = (actor.zone, actor.position)
        if "steps" in names and state["pos"] is not None and pos != tuple(state["pos"]):
            state["steps"] += 1
        state["pos"] = list(pos)
        beat = self.script.beats[state["beat"]]
        candidates = [*beat.next, *(t for ln in beat.lines if ln.id not in state["fired"] for t in ln.when)]
        for trigger in candidates:
            if trigger.name not in names:
                continue
            if self.holds(trigger, state, actor, in_hall, tick):
                self.fire(session, state, trigger.name, trigger.value or True)
                if state["beat"] != self.script.beats.index(beat):
                    return

    def holds(self, trigger: s.ScriptTrigger, state: dict[str, Any], actor: Any, in_hall: bool, tick: int) -> bool:
        if trigger.name == "region":
            if not in_hall or trigger.region is None:
                return False
            x, y, w, h = trigger.region
            return x <= actor.x < x + w and y <= actor.y < y + h
        if trigger.name == "steps":
            return state["steps"] >= trigger.count
        if trigger.name == "seconds":
            return tick - (state["beat_tick"] or tick) >= trigger.count * TICK_HZ
        if trigger.name == "target_set":
            return bool(actor.target)
        if trigger.name == "zone_entered":
            return actor.zone == trigger.value if trigger.value else not in_hall
        return False

    def speak_next(self, session: Session, state: dict[str, Any], tick: int) -> None:
        for at, (lid, _, due) in enumerate(state["queue"]):
            if due and tick < due:
                continue
            _, ln = self.lines[lid]
            if self.quiet(session, ln, tick):
                return
            state["queue"].pop(at)
            self.say(session, state, ln)
            return

    # --- hints ----------------------------------------------------------

    def hint(self, session: Session, player: Any | None) -> str:
        """The current lesson hint for the objective slot, or ""."""
        state = self.states.get(session.id)
        if state is None or state.get("done") or not self.script:
            return ""
        beat = self.script.beats[state["beat"]]
        facts = set(state["facts"])
        keymap = next((k for k in self.game.content.keymaps.keymaps if k.id == session.keymap_id), None) \
            if self.game.content.keymaps else None
        for index, hint in enumerate(beat.hints):
            key = f"{beat.id}:{index}"
            if key in state["hints"] or (hint.after and hint.after not in facts):
                continue
            if hint.until and hint.until in facts:
                state["hints"].append(key)
                continue
            return resolve_keys(hint.text, keymap)
        return ""

    def key_used(self, session: Session, action: str) -> None:
        """A lesson hint goes once its key has been used (never again for that character)."""
        state = self.states.get(session.id)
        if state is None or not self.script or not action:
            return
        beat = self.script.beats[state["beat"]]
        facts = set(state["facts"])
        for index, hint in enumerate(beat.hints):
            key = f"{beat.id}:{index}"
            if key in state["hints"] or (hint.after and hint.after not in facts):
                continue
            if hint.key and (hint.key == action or hint.key == "move" and action.startswith(("move_", "sprint_"))):
                state["hints"].append(key)
            break
