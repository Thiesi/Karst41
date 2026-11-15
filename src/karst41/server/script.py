"""S48c2: the opening's beats runner (#220 section 4; technical lead's contract).

One runner state per caller, for the opening only. It starts when a caller
is put in the Wake Hall with an unread body, and ends when Vesper's talk
page first opens, or when the caller detaches after the record has been
read and the hall left (only the errand, a per-character flag, goes on).

- Speech (Vantongeren, Okafor) is queued and paced: at least four seconds
  between two lines to this caller, from any source (`session.voice_tick`
  is shared with P8). Every other kind is said at once; one chained with a
  `delay` waits until due and is then said at once, never gated like
  speech and without setting `voice_tick` (fix 1 as amended 2026-11-14,
  owner-approved). A line with both `when` and `after` is armed by its
  trigger and follows its `after` line only once armed; armed after that
  line was already spoken, it is queued at once (second amendment).
- When a beat begins, its own lines go first; waiting non-anchor lines of
  earlier beats are dropped. An older anchor is spoken only when no line
  of the current beat is waiting, oldest first; a chained line goes ahead
  of older anchors, as its lead did. Once the record opens, the drone
  beat's anchor (6.2), if still owed, plays first instead and may speak
  under the record (fourth amendment, 2026-11-15, owner-approved), and a
  non-anchor follower of a line from before the record is stale and never
  queued (technical lead). Other owed anchors (3.1, 3.3) follow 9.9, and
  the kit waits for them: 9.10 comes its delay after the last of them
  (fifth amendment, 2026-11-15, owner-approved).
- Quiet for speech: a target set, an ICE warned, a menu or text page open,
  jacked in, or within P8's ten-second danger hold. The drone lesson's
  lines hold only while the drone can still act: down, skipped or inert
  ends the hold (third amendment, 2026-11-15, owner-approved;
  `karst41.server.danger`). `fight` lines may speak during the drone
  fight; `record` lines while the record or the name prompt is open.
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
import re
from typing import Any

from karst41.content import schema as s
from karst41.hints import resolve_keys
from karst41.server.clock import TICK_HZ
from karst41.server.danger import TRAINING, holds, released
from karst41.server.loop import TickContext
from karst41.server.sessions import Session

NAMED = re.compile(r"\{(item|hymn):([a-z_0-9]+)\}")
GAP_SECONDS = 4
SCRIPT_ID = "wake-opening"


def fresh() -> dict[str, Any]:
    """A runner state, as stored in the wake record and later in `progress`."""
    return {"beat": 0, "spoken": [], "fired": [], "armed": [], "deferred": [], "queue": [], "facts": [], "hints": [],
            "beat_tick": None, "steps": 0, "pos": None, "read": False, "done": False}


READ_BACK = "read-back"  # The one-time Ctrl-P row (S48c3b).
READ_BACK_SECONDS = 8


class Opening:
    def __init__(self, core: Any) -> None:
        self.core, self.game = core, core.characters
        self.script: s.Script | None = self.game.content.scripts.get(SCRIPT_ID)
        self.states: dict[str, dict[str, Any]] = {}
        self.showing: dict[str, tuple[str, int]] = {}
        self.walked: dict[str, list[Any]] = {}
        self.missed_heard: set[str] = set()  # Connections that sent `log_missed` (S48c3b).
        self.lines: dict[str, tuple[int, s.ScriptLine]] = {}
        self.after: dict[str, list[str]] = {}
        self.record_beat = len(self.script.beats) if self.script else 0
        self.drone_beat = -1
        if self.script:
            for index, beat in enumerate(self.script.beats):
                for ln in beat.lines:
                    self.lines[ln.id] = index, ln
                    if ln.after:
                        self.after.setdefault(ln.after, []).append(ln.id)
            self.record_beat = next((i for i, beat in enumerate(self.script.beats) if beat.id == "the-record"),
                                    self.record_beat)
            self.drone_beat = next((i for i, beat in enumerate(self.script.beats) if beat.id == "the-drone"), -1)
        hook = self.game.lesson_hook
        self.game.lesson_hook = lambda player, key, value: (hook(player, key, value), self.push_player(player, key, value))

    # --- lifetime -------------------------------------------------------

    def start(self, session: Session, state: dict[str, Any]) -> None:
        """Attach a caller's stored state (fresh or resumed)."""
        if not self.script or state.get("done"):
            return
        state.setdefault("armed", [])  # States saved before the fix 1 amendment.
        state["deferred"] = []  # Resume drops what waited on owed anchors, like waiting lines.
        # A saved state may name a beat or line the content no longer has: clamp
        # the beat and drop unknown lines, so an old record never blocks login.
        state["beat"] = max(0, min(int(state.get("beat", 0) or 0), len(self.script.beats) - 1))
        if state.get("queue"):  # Resume: waiting non-anchors are dropped (technical lead, ruling b).
            # Saved ticks belong to the process that wrote them (RealClock counts
            # from start-up), so a kept anchor is due now, not at an old tick.
            state["queue"] = [[q[0], q[1], 0] for q in state["queue"]
                              if q[0] in self.lines and self.lines[q[0]][1].anchor]
        if state.get("beat_tick") is None:
            state["beat_tick"] = self.core.clock.now_tick()
            self.begin(session, state, state["beat"])
        else:
            # The same for the beat's own clock: a `seconds` trigger counts from
            # this attach, never from a tick of an earlier process.
            state["beat_tick"] = self.core.clock.now_tick()
        state["pos"] = None
        self.states[session.id] = state
        pending = state.pop("kit_pending", None)
        if pending is not None:  # A commit that found no session (review 6889245226).
            self.push(session, "kit_issued", pending)

    def stop(self, session: Session) -> dict[str, Any] | None:
        # The one-time row on screen and the steps walked toward one are this
        # connection's only (review 6889691584); the flags live on the character.
        self.showing.pop(session.id, None)
        self.walked.pop(session.id, None)
        self.missed_heard.discard(session.id)
        return self.states.pop(session.id, None)

    def log_missed(self, session: Session) -> None:
        """The door saw a spoken line cut or pushed out of view before it was
        drawn whole (architecture 3, S48c3b): the in-hall Ctrl-P read-back.

        Only while the opening is live, in the hall, and once per character:
        the flag rides in the runner state, which goes on with the character,
        so Clinic Row's fallback never shows it again. Anything else is
        ignored silently.
        """
        if session.id in self.missed_heard:
            return  # The first per connection is honoured; the rest are dropped.
        self.missed_heard.add(session.id)
        state = self.states.get(session.id)
        if not self.script or state is None or state.get("done") or READ_BACK in state["hints"]:
            return
        player = self.game.players.get(session.player_id)
        avatar = self.game.sim.avatars.get(session.user_id)
        if (player is None or READ_BACK in player.progress.get("hints_seen", [])
                or avatar is None or avatar.actor.zone != self.script.zone
                or not any(h.id == READ_BACK for h in self.script.hints)):
            return
        state["hints"].append(READ_BACK)
        self.showing[session.id] = READ_BACK, self.core.clock.now_tick() + READ_BACK_SECONDS * TICK_HZ

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

    def fire(self, session: Session, state: dict[str, Any], name: str, value: Any,
             only: s.ScriptTrigger | None = None) -> None:
        """A pushed fact matches by name and value. A polled trigger (`only`)
        fires just the line or beat it belongs to: its own region, count or
        target, never every trigger of that name."""
        beats = self.script.beats

        def hit(trigger: s.ScriptTrigger) -> bool:
            return trigger is only if only is not None else self.matches(trigger, name, value)
        matched = [lid for lid, (_, ln) in self.lines.items()
                   if lid not in state["fired"] and any(hit(t) for t in ln.when)]
        # Arm chained lines first, so a line said in this same call finds
        # its followers armed (fix 1 as amended).
        armed = state.setdefault("armed", [])
        armed.extend(lid for lid in matched if self.lines[lid][1].after and lid not in armed)
        # Armed after its `after` line was already spoken: queued at once
        # (fix 1, second amendment, owner-approved 2026-11-14).
        for lid in matched:
            ln = self.lines[lid][1]
            if ln.after and ln.after in state["spoken"] and lid not in state["fired"]:
                state["fired"].append(lid)
                if self.eligible(state, ln) and not self.stale(state, lid):
                    self.queue_line(session, state, lid, front=True)
        # A triggered line, in any beat (the record can open from beat 3).
        for lid in matched:
            index, ln = self.lines[lid]
            if ln.after:
                continue
            if not self.eligible(state, ln):
                state["fired"].append(lid)
                continue
            state["fired"].append(lid)
            if index > state["beat"]:
                self.begin(session, state, index)
            self.queue_line(session, state, lid, front=True)
        # A beat ends. A pushed fact may end a later beat too: a caller who
        # walks straight to the terminal skips beats 2-7 (every lesson can be
        # skipped, game design 4). Polled triggers only ever end the current beat.
        start = state["beat"]
        if name in s.SCRIPT_POLLED:
            ends = [start] if any(hit(t) for t in beats[start].next) else []
        else:
            ends = [i for i in range(start, len(beats)) if any(hit(t) for t in beats[i].next)]
        if ends and ends[0] + 1 < len(beats) and state["beat"] == start:
            self.begin(session, state, ends[0] + 1)

    def eligible(self, state: dict[str, Any], ln: s.ScriptLine) -> bool:
        facts = set(state["facts"])
        return all(f in facts for f in ln.requires) and not any(f in facts for f in ln.unless)

    def past_record(self, state: dict[str, Any], lid: str) -> bool:
        """`lid` is from a beat before the record, and the record has opened."""
        return self.lines[lid][0] < self.record_beat <= state["beat"]

    def stale(self, state: dict[str, Any], lid: str) -> bool:
        """A non-anchor follower of an earlier beat's line, once the record has
        opened: it only makes sense straight after its lead in the lesson
        (technical lead, 2026-11-15; 6.3, 6.5, 6.4)."""
        return self.past_record(state, lid) and not self.lines[lid][1].anchor

    def owed(self, state: dict[str, Any], lid: str) -> bool:
        """The drone beat's anchor (6.2) still owed once the record has opened:
        it plays first, ahead of the record lines, and may speak under the
        record (amendment 4, owner-approved 2026-11-15). Any other owed anchor
        (3.1, 3.3) waits and follows 9.8 and 9.9 (first-ten-minutes, Pacing)."""
        index, ln = self.lines[lid]
        return index == self.drone_beat and ln.anchor and self.past_record(state, lid)

    def owed_anchors(self, state: dict[str, Any], lid: str) -> bool:
        """Amendment 5 (owner-approved 2026-11-15): after the record, a timed
        event line chained to a line of the record or later (9.10 after 9.9)
        waits while anchors from before the record are still owed (3.1, 3.3)."""
        return self.lines[lid][0] >= self.record_beat <= state["beat"] and any(
            self.lines[q[0]][1].anchor and self.lines[q[0]][0] < self.record_beat for q in state["queue"])

    def release(self, session: Session, state: dict[str, Any]) -> None:
        """Queue deferred lines once no owed anchor waits: each `delay` after
        the last of them was spoken; later links keep their own spacing."""
        deferred = state.get("deferred")
        if not deferred or any(self.lines[q[0]][1].anchor and self.lines[q[0]][0] < self.record_beat
                               for q in state["queue"]):
            return
        state["deferred"] = []
        now = self.core.clock.now_tick()
        for lid in deferred:
            self.queue_line(session, state, lid, front=True, due=now + round(self.lines[lid][1].delay * TICK_HZ))

    def begin(self, session: Session, state: dict[str, Any], index: int) -> None:
        state["beat"], state["beat_tick"], state["steps"] = index, self.core.clock.now_tick(), 0
        # Waiting speech of earlier beats is dropped unless it is an anchor. A
        # delayed event line is not waiting speech: it plays when due, and
        # only the hall drop removes it (fix 1 as amended; 9.11 after kit_issued).
        state["queue"] = [q for q in state["queue"] if q[1] >= index or self.lines[q[0]][1].anchor
                          or self.lines[q[0]][1].kind != "speech"]
        # Amendment 4: an owed anchor from before the record goes first.
        state["queue"].sort(key=lambda q: not self.owed(state, q[0]))
        for ln in self.script.beats[index].lines:
            if not ln.when and not ln.after and ln.id not in state["fired"]:
                state["fired"].append(ln.id)
                if self.eligible(state, ln):
                    self.queue_line(session, state, ln.id)

    def queue_line(self, session: Session, state: dict[str, Any], lid: str, front: bool = False,
                   due: int = 0) -> None:
        index, ln = self.lines[lid]
        if ln.kind != "speech" and due <= self.core.clock.now_tick():
            self.say(session, state, ln)  # Event lines come at once (creative lead, fix 1).
            return
        entry = [lid, index, due]
        if front:  # Ahead of older anchors, behind the current beat's waiting lines.
            queue = state["queue"]
            lead = 0  # Behind an owed anchor from before the record (amendment 4).
            while lead < len(queue) and self.owed(state, queue[lead][0]):
                lead += 1
            at = next((i for i in range(lead, len(queue)) if queue[i][1] < state["beat"]), len(queue))
            queue.insert(at, entry)
        else:
            state["queue"].append(entry)

    # --- speaking -------------------------------------------------------

    def text(self, ln: s.ScriptLine) -> str:
        """`{item:<id>}` and `{hymn:<id>}` take the content's own name (creative lead)."""
        content = self.game.content
        def name(match: re.Match[str]) -> str:
            kind, ref = match.group(1), match.group(2)
            table = content.items if kind == "item" else content.hymns
            return table[ref].name
        return NAMED.sub(name, ln.text)

    def say(self, session: Session, state: dict[str, Any], ln: s.ScriptLine) -> None:
        at = self.core.clock.now_utc().isoformat()
        if ln.kind == "speech":
            name = ln.name or self.script.speakers.get(ln.speaker) or self.game.content.dialogue[ln.speaker].name
            session.emit(f"{name}: {self.text(ln)}", kind="speech", channel="zone", role="chan_zone", at=at,
                         speaker=name, speaker_role="service", script=ln.id)
            session.voice_tick = self.core.clock.now_tick()  # P8 stays quiet behind it.
        else:
            role = {"danger": "warning", "reward": "chan_sys", "combat": "chan_sys"}.get(ln.kind, "chan_sys")
            channel = "combat" if ln.kind in ("danger", "combat") else "system"
            # The opening's danger and combat lines are the drone lesson's
            # (6.1, 6.6), so their hold ends with the drone (amendment 3).
            source = TRAINING + session.player_id if ln.kind in ("danger", "combat") and session.player_id else None
            session.emit(self.text(ln), kind=ln.kind, channel=channel, role=role, at=at, script=ln.id,
                         source=source)
        state["spoken"].append(ln.id)
        for follower in self.after.get(ln.id, ()):
            _, nxt = self.lines[follower]
            if nxt.when and follower not in state.get("armed", ()):
                continue  # Its own trigger never fired (another archetype's line).
            if follower not in state["fired"]:
                state["fired"].append(follower)
                if not self.eligible(state, nxt) or self.stale(state, follower):
                    continue
                if nxt.kind != "speech" and self.owed_anchors(state, follower):
                    # Amendment 5: the kit waits for owed lease anchors.
                    state.setdefault("deferred", []).append(follower)
                    continue
                # Ahead of older anchors, as its lead was: 9.9 follows 9.8
                # before an owed 3.1/3.3 (first-ten-minutes, Pacing).
                self.queue_line(session, state, follower, front=True,
                                due=self.core.clock.now_tick() + round(nxt.delay * TICK_HZ))
        self.release(session, state)

    def quiet(self, session: Session, ln: s.ScriptLine, tick: int) -> bool:
        """Speech only (creative lead, fix 1)."""
        if session.voice_tick is not None and tick - session.voice_tick < GAP_SECONDS * TICK_HZ:
            return True
        avatar = self.game.sim.avatars.get(session.user_id)
        actor = avatar.actor if avatar else None
        fight = ln.fight and any(a.owner == session.player_id and a.behaviour.get("training")
                                 for a in self.core.world.actors.values())
        if not fight and holds(session, self.core.clock.now_utc(), self.game.training_drone_can_act):
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
        state = self.states.get(session.id)
        record = ln.record or (state is not None and self.owed(state, ln.id))  # Amendment 4.
        if menu and not (record and record_open):
            return True
        if not record and record_open:
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
            if not in_hall and (state["queue"] or state.get("deferred")):
                state["queue"] = []  # Nothing from the hall plays outside it (creative lead, fix 3).
                state["deferred"] = []
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
                self.fire(session, state, trigger.name, trigger.value or True, only=trigger)
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
            began = state["beat_tick"] if state["beat_tick"] is not None else tick  # Tick 0 is a tick.
            return tick - began >= trigger.count * TICK_HZ
        if trigger.name == "target_set":
            return bool(actor.target)
        if trigger.name == "zone_entered":
            return actor.zone == trigger.value if trigger.value else not in_hall
        return False

    def speak_next(self, session: Session, state: dict[str, Any], tick: int) -> None:
        # A delayed event line plays once due, never gated like speech (fix 1 as amended).
        for entry in [q for q in state["queue"] if self.lines[q[0]][1].kind != "speech" and tick >= q[2]]:
            if entry in state["queue"]:
                state["queue"].remove(entry)
                self.say(session, state, self.lines[entry[0]][1])
        # A held line holds the queue behind it, except a `fight` line, which
        # may pass a held ordinary line: 6.5 speaks at the first hit while 6.3
        # waits out the fight (approved design, Pacing and beat 6).
        held = False
        for at, (lid, _, due) in enumerate(state["queue"]):
            if due and tick < due:
                continue
            _, ln = self.lines[lid]
            if ln.kind != "speech" or (held and not ln.fight):
                continue
            if self.quiet(session, ln, tick):
                if ln.fight:
                    return
                held = True
                continue
            state["queue"].pop(at)
            self.say(session, state, ln)
            return

    # --- the hint row (terminal UI 11; presentation lead's build) -----------

    def decorate(self, session: Session, body: dict[str, Any]) -> None:
        """The objective slot: a lesson hint, else the errand, else a one-time hint."""
        if not self.script:
            return
        stamp = released(session, self.core.clock.now_utc(), self.game.training_drone_can_act)
        if stamp and (session.danger_release is None
                      or datetime.fromisoformat(stamp) > datetime.fromisoformat(session.danger_release)):
            session.danger_release = stamp  # Only ever forward (terminal UI 2.5, #253).
        if session.danger_release and "viewport" in body:  # Amendment 3 in the client's log.
            body.setdefault("me", {})["danger_release"] = session.danger_release
        if "viewport" not in body:  # The Lattice view: lesson hints only (beat 8).
            text = self.hint(session, self.game.players.get(session.player_id))
            if text:
                body["lesson"] = {"text": text}
            return
        player = self.game.players.get(session.player_id)
        keymap = next((k for k in self.game.content.keymaps.keymaps if k.id == session.keymap_id), None) \
            if self.game.content.keymaps else None
        showing = self.showing.get(session.id)
        if showing and showing[0] == READ_BACK and self.core.clock.now_tick() < showing[1]:
            # The in-hall read-back (S48c3b), over a lesson hint for its 8 s.
            body["lesson"] = {"text": resolve_keys(next(h.text for h in self.script.hints if h.id == READ_BACK), keymap)}
            return
        text = self.hint(session, player)
        if text:
            body["lesson"] = {"text": text}
            return
        if player is None:
            return
        avatar = self.game.sim.avatars.get(session.user_id)
        zone = avatar.actor.zone if avatar else None
        once =self.once_hint(session, player, zone, avatar) if player.progress.get("opening") is not None else ""
        if once:
            body["lesson"] = {"text": resolve_keys(once, keymap)}
            return
        if player.progress.get("errand"):
            errand = next((e for e in self.script.errands if e.zone == zone), None)
            if errand is not None:
                body["lesson"] = {"text": resolve_keys(errand.text, keymap), "short": errand.short}

    def once_hint(self, session: Session, player: Any, zone: str | None, avatar: Any) -> str:
        """One-time rows, kept as per-character flags (creative lead): never runner state."""
        seen = player.progress.setdefault("hints_seen", [])
        if READ_BACK in (player.progress.get("opening") or {}).get("hints", []) and READ_BACK not in seen:
            seen.append(READ_BACK)  # Shown in the hall already (S48c3b): never again.
        tick = self.core.clock.now_tick()
        showing = self.showing.get(session.id)
        if showing:
            hint_id, until = showing
            if tick < until:
                return next(h.text for h in self.script.hints if h.id == hint_id)
            self.showing.pop(session.id, None)
        for hint in self.script.hints:
            if hint.id in seen:
                continue
            if hint.zone and hint.zone != zone:
                return ""
            if hint.steps:
                walked = self.walked.setdefault(session.id, [None, 0])
                pos = (zone, avatar.actor.position) if avatar else None
                if walked[0] is not None and pos != walked[0]:
                    walked[1] += 1
                walked[0] = pos
                if walked[1] < hint.steps:
                    return ""
            if hint.zone or any(h.id in seen for h in self.script.hints if h is not hint):
                seen.append(hint.id)
                self.showing[session.id] = hint.id, tick + max(1, hint.seconds or 8) * TICK_HZ
                return hint.text
            return ""
        return ""

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
