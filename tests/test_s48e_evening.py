"""S48e: the first evening, walked by keys on the production Core.

S48's Goal, in its order: wake; reach the Row; take Vesper's first job and
finish it both ways (by hand, and by jacking in); get paid; take one
generated job; die once; log out. Shipped content, spawners on, one Core.
Every caller acts only through the door model's keys, which turn into the
frames and intents a real client sends; the test reads server state only to
know where to walk and what to check.

Callers (technical lead's rulings (a), (b), (d)):
- Noor (80x24) does every lesson, Vesper's job by hand, then the board's
  first generated offer, then breaches the Row truce and dies; Sable answers.
- Ilse (132x50) does every lesson and Vesper's job by jacking in.
- Tam (80x24) skips every lesson and does Vesper's job by hand.
- Vex (132x50), off the evening path, skips every lesson, logs out on Clinic
  Row (which ends the opening's runner), dials back in and goes straight to
  the Row breach: a death under the wall voice's per-evening cap.

Each caller's frames also drive a second, legacy door model at the same
size, so every hint row on the path is checked in both renderers at 80x24
and 132x50. The walk fails at the first step that dead-ends, naming the
caller and the step; every wait is bounded.
"""

from collections import deque
from contextlib import contextmanager
from pathlib import Path
from random import Random
import tempfile

import pytest

from karst41.content.loader import load_content
from karst41.door.doorinfo import DoorInfo
from karst41.door.model import Display
from karst41.server.clock import TICK_HZ
from karst41.server import truce
from tests.fake_terminal import FakeTerminal
from tests.harness import HeadlessServer

ROOT = Path(__file__).parents[1]
HALL, HALO, ROW = 'vatside-wake-hall', 'sodium-tin-halo', 'sodium-row'
TO_HALO = ('vatside-clinics', 'core-plaza', ROW, HALO)
ARROWS = {(1, 0): 'right', (-1, 0): 'left', (0, 1): 'down', (0, -1): 'up'}
WAKE_SECONDS = 5*60  # Game design 4: the Wake completes in five minutes.
CUT = ('...', '…')


class Walked(AssertionError):
    """A dead end, named by caller and step."""


class Caller:
    def __init__(self, game: HeadlessServer, uid: int, name: str, width: int, height: int, seen: dict) -> None:
        self.game, self.name, self.width, self.height, self.seen = game, name, width, height, seen
        raw = {'user_id': uid, 'handle': name, 'node_name': 'S48e'}
        self.client = game.connect(raw, width=width, height=height)
        self.session = self.client.session
        folder = Path(tempfile.mkdtemp(prefix='s48e-'))
        info = DoorInfo(folder/'door_info.json', folder, raw, width, height, '256', 3600)
        self.models = {'present': Display(info, presentation=True), 'legacy': Display(info, presentation=False)}
        self.terminals = {kind: FakeTerminal(width, height) for kind in self.models}
        self.log: list[dict] = []
        self.toasts: list[str] = []
        self.checked = None  # What the hint row was last checked from.
        self.step = 'connect'
        self.connected_at = self.now()

    def now(self) -> float:
        return self.game.clock.now_tick()/TICK_HZ

    @property
    def actor(self):
        return self.game.core.world.actors.get(self.session.player_id)

    def body(self) -> dict:
        return (self.game.core.characters.sim.views[self.session.id].previous or {}).get('body', {})

    def drain(self) -> None:
        now = self.now()
        for frame in self.game.frames_for(self.client):
            if frame.type == 'log' and not frame.data.get('replay'):
                self.log.extend(frame.data['lines'])
            if frame.type == 'toast':
                self.toasts = [*self.toasts[-7:], frame.data.get('text', '')]
            for model in self.models.values():
                model.receive(frame, now)

    def screens(self) -> dict[str, str]:
        self.drain()
        now = self.now()
        for kind, model in self.models.items():
            out, _ = model.render(now)
            self.terminals[kind].write(out)
        return {kind: terminal.screen_text() for kind, terminal in self.terminals.items()}

    def screen(self) -> str:
        return self.screens()['present']

    def press(self, key: str) -> None:
        self.drain()
        if key in ('e', 'E'):
            self.check_focus()
        now = self.now()
        frames = self.models['present'].key(key, now)
        self.models['legacy'].key(key, now)  # Its own frames are not sent: one client per caller.
        for frame in frames:
            self.game.core.receive(self.session, frame)
        body = self.body()
        drawn = repr((body.get('hint'), body.get('focus'), body.get('jack_focus'), body.get('objective'),
                      body.get('lesson'), self.toasts, self.step))
        # Only a plain step on the map may skip the check, and only while what
        # the hint row is made of is unchanged; every other key, every such
        # change and every step's start (Walk.step) is checked.
        if key not in ARROWS.values() or drawn != self.checked:
            self.checked = drawn
            self.check_screen()

    def check_focus(self) -> None:
        """Terminal UI 11: whatever E acts on is named, never by an internal id."""
        focus = self.body().get('focus')
        if focus is not None and (not focus.get('label') or focus['label'] == focus.get('id')):
            self.seen['unnamed_focus'].append((self.name, self.step, focus))

    def check_screen(self) -> None:
        for kind, text in self.screens().items():
            rows = text.splitlines()
            hint = rows[-2].strip('║│ ') if len(rows) >= 2 else ''
            cut = next((hint.index(mark) for mark in CUT if mark in hint), None)
            if cut is None:
                continue
            toast = next((whole for whole in self.toasts if cut > 0 and whole.startswith(hint[:cut].rstrip())), None)
            if toast is not None:
                # A toast in the hint row, cut to leave the hint whole: its
                # full text must reach the log (P12).
                self.seen['cut_toasts'].append((self.name, kind, self.width, self.step, toast))
            else:
                self.seen['cut_hints' if kind == 'present' else 'legacy_cut_hints'].append(
                    (self.name, self.width, self.step, hint))
        for row in self.body().get('nearby') or ():
            if not row.get('name') or row.get('name') == row.get('id'):
                self.seen['unnamed_nearby'].append((self.name, self.step, row))


def tick(game: HeadlessServer, callers, n: int = 1) -> None:
    for _ in range(n):
        game.step()
        game.commit_characters()
        game.commit_commerce()
        for caller in callers:
            caller.drain()


class Walk:
    def __init__(self) -> None:
        self.game = HeadlessServer(load_content(ROOT/'src/karst41/content'), character_state={})
        self.callers: list[Caller] = []
        self.seen: dict = {'unnamed_focus': [], 'cut_hints': [], 'legacy_cut_hints': [], 'cut_toasts': [],
                           'unnamed_nearby': [], 'wake': {},
                           'lessons_at_record': {}, 'paid': {}, 'first_screen': {}, 'delivery_focus': {},
                           'generated': None, 'deaths': {}, 'walls': {}, 'left': {}, 'logs': {}}

    def tick(self, n: int = 1) -> None:
        tick(self.game, self.callers, n)

    @contextmanager
    def step(self, caller: Caller, name: str):
        caller.step = name
        caller.check_screen()  # Every lesson, page and checkpoint: the full hint row, unconditionally.
        try:
            yield
        except Walked:
            raise
        except (AssertionError, KeyError, StopIteration, ValueError) as exc:
            raise Walked(f'{caller.name} dead-ends at "{name}": {exc!r}') from exc

    # --- movement ---------------------------------------------------------

    def path(self, caller: Caller, dest, adjacent: bool = False):
        zone = self.game.core.world.zones[caller.actor.zone]
        me = caller.actor
        occupied = {a.position for a in zone.actors.values() if a is not me}
        exits = {e.tile for e in zone.template.meta.exits}
        if adjacent:
            goal = lambda t: max(abs(t[0]-dest[0]), abs(t[1]-dest[1])) <= 1 and t != dest
        else:
            goal = lambda t: t == dest
        start = me.position
        prev, queue = {start: None}, deque([start])
        end = start if goal(start) else None
        while queue and end is None:
            x, y = queue.popleft()
            for t in ((x+1, y), (x, y+1), (x-1, y), (x, y-1)):
                if t in prev or t in occupied or not zone.passable(t) or (t in exits and t != dest):
                    continue
                prev[t] = (x, y)
                queue.append(t)
                if goal(t):
                    end = t
                    break
        if end is None:
            return None
        steps = []
        while prev[end] is not None:
            steps.append(end)
            end = prev[end]
        return steps[::-1]

    def walk(self, caller: Caller, dest, adjacent: bool = False, limit: int = 3000) -> None:
        zid = caller.actor.zone
        for _ in range(limit):
            me = caller.actor
            if me is None or me.zone != zid:
                return
            route = self.path(caller, dest, adjacent)
            if route == []:
                return
            if route is None:
                self.tick(2)
                continue
            cooldown = max(me.cooldowns.get('move', 0), me.cooldowns.get('knockdown', 0))
            for _ in range(200):
                if self.game.clock.now_tick() >= cooldown:
                    break
                self.tick()
            before = me.position
            caller.press(ARROWS[(route[0][0]-before[0], route[0][1]-before[1])])
            self.tick()
        raise AssertionError(('no way to', zid, caller.actor.position, dest))

    def travel(self, caller: Caller, dest: str, nearest: bool = False) -> None:
        """Onto the exit and E. #211: a bounded wait for the exit's focus."""
        origin = caller.actor.zone
        exits = [e for e in self.game.core.world.zones[origin].template.meta.exits if e.target_zone == dest]
        assert exits, ('no exit', origin, dest)
        exit_ = (min(exits, key=lambda e: len(self.path(caller, e.tile) or [0]*9999)) if nearest else exits[0])
        self.walk(caller, exit_.tile)
        for _ in range(100):
            if caller.actor.zone == dest:
                break
            focus = caller.body().get('focus') or {}
            if caller.actor.position == exit_.tile and focus.get('id') == exit_.id:
                caller.press('e')
            self.tick()
        self.tick(3)
        assert caller.actor.zone == dest, ('travel', origin, dest, caller.actor.position)

    def route(self, caller: Caller, *zones: str) -> None:
        for zone in zones:
            with self.step(caller, 'to '+zone):
                self.travel(caller, zone)

    # --- the opening --------------------------------------------------------

    def first_screen(self, caller: Caller) -> None:
        with self.step(caller, 'first screen'):
            screen = caller.screen()
            zone = self.game.core.world.zones[HALL].template.meta.name
            assert zone in screen.splitlines()[0], screen.splitlines()[0]
            before = caller.actor.position
            caller.press('right')
            self.tick(3)
            self.seen['first_screen'][caller.name] = (before, caller.actor.position)

    def own_drone(self, caller: Caller) -> bool:
        zone = self.game.core.world.zones[HALL]
        return any(a.id.startswith(caller.session.player_id) and 'training' in a.id and a.health > 0
                   for a in zone.actors.values())

    def drone(self, caller: Caller, skip: bool) -> None:
        with self.step(caller, 'skip the drone' if skip else 'the drone'):
            self.walk(caller, (21, 14))
            self.tick(10)
            assert self.own_drone(caller), 'the training drone did not wake'
            for _ in range(400):
                if not self.own_drone(caller):
                    return
                caller.press('b' if skip else ('f' if caller.body().get('target') else 'tab'))
                self.tick(3)
            raise AssertionError('the drone never went down')

    def terminal(self, caller: Caller) -> None:
        lat, pid = self.game.core.lattice, caller.session.player_id
        with self.step(caller, 'jack in at the Hall terminal'):
            self.walk(caller, (35, 15), adjacent=True)
            self.tick(5)
            caller.press('j')
            self.tick(10)
            assert pid in lat.presences, caller.screen().splitlines()[-2]
        with self.step(caller, 'the training ICE'):
            caller.press('tab')
            self.tick()
            for _ in range(20):
                presence = lat.presences[pid]
                defenders = lat.encounters.defenders(presence)
                if not defenders or not defenders[0].alive:
                    break
                caller.press('1')
                self.tick()
                presence = lat.presences[pid]
                self.tick(max(1, presence.cast.until-self.game.clock.now_tick()+3) if presence.cast else 10)
            else:
                raise AssertionError('the training ICE never fell')
        with self.step(caller, 'lift the record'):
            caller.press('e')
            self.tick(3)
            caller.press('enter')
            self.tick(45)
            for _ in range(10):
                if pid not in lat.presences:
                    break
                caller.press('b')
                self.tick(20)
            assert pid not in lat.presences, 'B did not leave the training core'

    def door(self, caller: Caller) -> None:
        with self.step(caller, 'E at the street door'):
            self.walk(caller, (38, 16))
            self.tick(3)
            caller.press('e')
            self.tick(5)
            assert 'DECANT RECORD' in caller.screen().splitlines()[0]

    def record(self, caller: Caller, archetype: int) -> None:
        with self.step(caller, 'the record'):
            assert 'DECANT RECORD' in caller.screen().splitlines()[0], caller.screen().splitlines()[0]
            wake = self.game.core.characters.wakes[caller.session.id]
            self.seen['lessons_at_record'][caller.name] = dict(wake.lessons)
            for _ in range(archetype):
                caller.press('down')
                self.tick()
            caller.press('enter')
            self.tick(3)
            for _ in range(200):
                if 'Name' in caller.screen().splitlines()[-2]:
                    break
                self.tick(5)
            else:
                raise AssertionError('no name prompt')
            for ch in caller.name:
                caller.press(ch)
            caller.press('enter')
            self.tick(15)
            player = self.game.core.characters.players.get(caller.session.player_id)
            assert player is not None and player.archetype, 'the character was not made'
            self.seen['wake'][caller.name] = self.game.clock.now_tick()/TICK_HZ-caller.connected_at

    # --- Vesper -------------------------------------------------------------

    def vesper(self):
        zone = self.game.core.world.zones[HALO]
        return next(a for a in zone.actors.values()
                    if a.behaviour.get('template') == 'npc-vendor-sodium-tin-halo-vesper')

    def talk_to_vesper(self, caller: Caller) -> None:
        for _ in range(5):
            if caller.session.id in self.game.core.dialogue.menus:
                return
            self.walk(caller, self.vesper().position, adjacent=True)
            self.tick(2)
            caller.press('e')
            self.tick(3)
        raise AssertionError("E beside Vesper did not open her page")

    def board(self, caller: Caller) -> None:
        self.talk_to_vesper(caller)
        caller.press('c')
        self.tick(3)
        assert caller.session.id in self.game.core.contracts.panels.menus, 'c did not open the Work board'

    def cursor_to(self, caller: Caller, row: str) -> None:
        panels = self.game.core.contracts.panels
        for _ in range(30):
            panel = panels.menus[caller.session.id]
            if panels.rows(caller.session, panel)[panel.cursor]['id'] == row:
                return
            caller.press('down')
            self.tick()
        raise AssertionError(('board row never under the cursor', row))

    def job(self, caller: Caller, template: str):
        owner, pid = self.game.core.contracts, caller.session.player_id
        return next((j for j in owner.state.engine.instances.values()
                     if j.template == template and pid in {p.player for p in j.roster}), None)

    def turn_in(self, caller: Caller, template: str) -> int:
        player = self.game.core.characters.players[caller.session.player_id]
        chits = player.chits_hand
        self.board(caller)
        self.cursor_to(caller, self.job(caller, template).id)
        caller.press('t')
        self.tick(2)
        caller.press('y')
        self.tick(5)
        caller.press('escape')
        self.tick(3)
        assert self.job(caller, template).state == 'turned_in', self.job(caller, template).state
        return player.chits_hand-chits

    def first_job(self, caller: Caller, by: str) -> None:
        stage = lambda: self.job(caller, 'vesper-first-thing')
        with self.step(caller, "take Vesper's job"):
            self.board(caller)
            self.cursor_to(caller, 'vesper-first-thing')
            caller.press('a')
            self.tick(3)
            caller.press('y')
            self.tick(5)
            assert stage() is not None, 'the job was not taken'
            caller.press('escape')
            self.tick(3)
        self.route(caller, ROW, 'core-plaza', 'terraces-blocks')
        with self.step(caller, 'check the parcel hatch'):
            self.walk(caller, (73, 25))
            self.tick(2)
            caller.press('e')
            self.tick(5)
            assert stage().index == 1, stage().index
        if by == 'hand':
            with self.step(caller, 'release the hatch by hand'):
                caller.press('e')
                self.tick(60)
                assert stage().index == 2, stage().index
        else:
            lat, pid = self.game.core.lattice, caller.session.player_id
            with self.step(caller, 'jack in at the loan terminal'):
                self.walk(caller, (71, 25), adjacent=True)
                self.tick(3)
                caller.press('j')
                self.tick(10)
                assert pid in lat.presences, caller.screen().splitlines()[-2]
            with self.step(caller, 'enter the cabinet core'):
                caller.press('e')
                self.tick(5)
                caller.press('enter')
                self.tick(10)
                assert lat.presences[pid].core == 'lat-stairwell-c', lat.presences[pid].core
            with self.step(caller, 'flip the parcel release'):
                caller.press('e')
                self.tick(5)
                caller.press('enter')
                self.tick(40)
                assert stage().index == 2, stage().index
            with self.step(caller, 'jack out'):
                for _ in range(10):
                    if pid not in lat.presences:
                        break
                    caller.press('j')
                    self.tick(30)
                assert pid not in lat.presences, 'J did not bring the body back'
        with self.step(caller, 'collect the parcel'):
            self.walk(caller, (72, 23), adjacent=True)
            self.tick(2)
            caller.press('e')
            self.tick(5)
            assert stage().index == 3, stage().index
        self.route(caller, 'core-plaza', ROW, HALO)
        with self.step(caller, 'hand the parcel over'):
            self.walk(caller, (8, 15), adjacent=True)
            self.tick(2)
            self.seen['delivery_focus'][caller.name] = caller.body().get('focus')
            caller.press('e')
            self.tick(5)
            assert stage().state == 'complete', stage().state
        with self.step(caller, "turn Vesper's job in"):
            self.seen['paid'][caller.name] = self.turn_in(caller, 'vesper-first-thing')

    def generated_job(self, caller: Caller) -> None:
        owner = self.game.core.contracts
        with self.step(caller, "take the board's first offer"):
            self.board(caller)
            offer = owner.offers('vesper')[0]
            self.cursor_to(caller, offer.id)
            caller.press('a')
            self.tick(3)
            caller.press('y')
            self.tick(5)
            assert self.job(caller, offer.template) is not None, 'offer 0 was not taken'
            caller.press('escape')
            self.tick(3)
        template = owner.state.engine.templates[offer.template]
        job = lambda: self.job(caller, template.id)
        # The walk's clock (the harness's, 2026-01-01 00:00) gives offer 0 as
        # "Mark the service approaches"; finish it as a player would.
        assert template.id == 'vesper-local-plant', template.id
        self.route(caller, ROW, 'core-plaza', 'terraces-blocks')
        with self.step(caller, 'collect the bound supply'):
            self.walk(caller, (103, 10), adjacent=True)
            self.tick(2)
            caller.press('e')
            self.tick(10)
            assert job().index == 1, job().index
        target = job().stages[1].targets[0]
        self.route(caller, 'core-plaza', ROW, target.zone)
        with self.step(caller, 'plant the supplied unit'):
            tile = self.game.core.world.zones[target.zone].objects[target.object].spec.tile
            self.walk(caller, tile, adjacent=True)
            self.tick(2)
            caller.press('e')
            self.tick(60)
            assert job().state == 'complete', job().state
        with self.step(caller, 'turn the generated job in'):
            self.seen['generated'] = (template.id, self.turn_in(caller, template.id))

    # --- the death ----------------------------------------------------------

    def breach(self, caller: Caller) -> None:
        with self.step(caller, 'draw and fire on the Row'):
            zone = self.game.core.world.zones[ROW]

            def regulars():
                near = []
                for actor in zone.actors.values():
                    if actor.kind == 'npc' and 'regular' in (actor.behaviour.get('template') or ''):
                        route = self.path(caller, actor.position, adjacent=True)
                        if route is not None:
                            near.append((len(route), actor.id, actor))
                return [actor for _, _, actor in sorted(near)]
            for _ in range(20):
                if regulars():
                    break
                self.tick(20)
            player = self.game.core.characters.players[caller.session.player_id]
            vat = self.game.core.combat.vat(player)
            heard = len(caller.log)
            for _ in range(20):  # Draw, then fire until the truce is broken (the keeper answers).
                if truce.hunted(player, self.game.core.combat.now()):
                    break
                mark = regulars()[0]
                self.walk(caller, mark.position, adjacent=True)
                self.tick(2)
                for _ in range(30):
                    if (caller.body().get('target') or {}).get('id') == mark.id:
                        break
                    caller.press('tab')
                    self.tick(2)
                caller.press('f')
                self.tick(25)  # A raised weapon fires after two seconds.
            assert truce.hunted(player, self.game.core.combat.now()), 'the shot never broke the truce'
        with self.step(caller, 'Sable answers'):
            for _ in range(6000):  # The caller stands still and takes the answer.
                self.tick()
                if caller.actor is None or caller.actor.zone != ROW:
                    break
            else:
                raise AssertionError('Sable never answered the breach')
        with self.step(caller, 'the decant'):
            for _ in range(3000):
                if caller.actor is not None and caller.actor.zone != ROW:
                    break
                self.tick()
            else:
                raise AssertionError('no decant after the death')
            self.tick(200)  # P8's danger hold and the decant grace, then the wall line.
            self.seen['deaths'][caller.name] = {
                'vat': vat, 'at': (caller.actor.zone, caller.actor.position),
                'lines': [line.get('text', '') for line in caller.log[heard:]]}

    def leave(self, caller: Caller) -> None:
        with self.step(caller, 'log out'):
            caller.press('ctrl-x')
            self.tick()
            caller.press('y')
            self.tick(5)
            self.seen['left'][caller.name] = caller.session.id not in self.game.core.sessions.active

    def walls(self, caller: Caller) -> None:
        self.seen['logs'].setdefault(caller.name, set()).update(line.get('text', '') for line in caller.log)
        lines = {line.text: line.id for line in self.game.core.characters.content.walls.lines}
        self.seen['walls'][caller.name] = [lines[line['text']] for line in caller.log if line.get('text') in lines]

    def run(self) -> dict:
        plan = (('Noor', 80, 24, 'hand', 0), ('Ilse', 132, 50, 'jack', 2),
                ('Tam', 80, 24, 'skip', 1), ('Vex', 132, 50, 'breach', 3))
        for uid, (name, width, height, by, archetype) in enumerate(plan, 1):
            caller = Caller(self.game, uid, name, width, height, self.seen)
            self.callers.append(caller)
            self.tick(5)
            self.first_screen(caller)
            if by in ('skip', 'breach'):
                self.drone(caller, skip=True)
                self.door(caller)
            else:
                self.drone(caller, skip=False)
                self.terminal(caller)
            self.record(caller, archetype)
            if by == 'breach':
                # The opening's runner keeps the wall voice quiet until Vesper's
                # page or a detach after the record outside the hall (S48c2,
                # S48d): Vex logs out on Clinic Row and dials back in.
                self.route(caller, 'vatside-clinics')
                self.leave(caller)
                self.tick(10)
                caller = Caller(self.game, uid, name, width, height, self.seen)
                self.callers.append(caller)
                self.tick(10)
                self.route(caller, 'core-plaza', ROW)
                self.breach(caller)
            else:
                self.route(caller, *TO_HALO)
                self.first_job(caller, 'jack' if by == 'jack' else 'hand')
                if name == 'Noor':
                    self.generated_job(caller)
                    self.route(caller, ROW)
                    self.breach(caller)
            self.walls(caller)
            self.leave(caller)
        return self.seen


@pytest.fixture(scope='module')
def evening() -> dict:
    return Walk().run()


# --- the evening, step by step ---------------------------------------------

def test_movement_works_on_the_first_screen(evening):
    for name, (before, after) in evening['first_screen'].items():
        assert after == (before[0]+1, before[1]), (name, before, after)


def test_the_archetype_comes_after_the_drone_and_the_terminal(evening):
    for name in ('Noor', 'Ilse'):
        lessons = evening['lessons_at_record'][name]
        assert lessons.get('drone') == 'done' and lessons.get('lifted'), (name, lessons)


def test_every_lesson_can_be_skipped(evening):
    for name in ('Tam', 'Vex'):
        lessons = evening['lessons_at_record'][name]
        assert lessons.get('drone') != 'done' and not lessons.get('lifted'), (name, lessons)
        assert evening['paid'].get(name, 0) or name == 'Vex'


def test_the_wake_completes_in_five_minutes(evening):
    assert set(evening['wake']) == {'Noor', 'Ilse', 'Tam', 'Vex'}
    for name, seconds in evening['wake'].items():
        assert seconds <= WAKE_SECONDS, (name, seconds)


def test_vespers_job_is_paid_by_hand_by_jack_and_after_skipping(evening):
    assert evening['paid'] == {'Noor': 200, 'Ilse': 200, 'Tam': 200}


def test_the_delivery_is_handed_over_by_name(evening):
    for name, focus in evening['delivery_focus'].items():
        assert focus == {'id': 'vesper', 'label': 'Stuck-door Package', 'verb': 'hand over'}, (name, focus)


def test_the_boards_first_generated_offer_is_finished_and_paid(evening):
    template, paid = evening['generated']
    assert template == 'vesper-local-plant' and paid > 0, evening['generated']


def test_the_first_evenings_death_wakes_in_the_nearest_vat_and_settles(evening):
    death = evening['deaths']['Noor']
    (zone, vat), (woke, tile) = death['vat'], death['at']
    assert zone == woke == 'core-precinct' and max(abs(vat[0]-tile[0]), abs(vat[1]-tile[1])) <= 1, death
    lines = death['lines']
    assert 'You wake in the vat. Missing recovery supplies are bound to you.' in lines
    assert any(line.startswith('Clone charge 50.') for line in lines), lines
    assert 'Sable considers it settled.' in lines


def test_the_first_evenings_death_gets_no_wall_line_once_the_cap_is_spent(evening):
    # Technical lead: the true first-evening experience. The cap (3) is spent
    # on the way (w14, w26, w18); a finding for the re-rank, not a fix here.
    walls = evening['walls']['Noor']
    assert len(walls) == 3 and not {'w10', 'w11'} & set(walls), walls


def test_a_death_under_the_cap_hears_the_decant_line(evening):
    death = evening['deaths']['Vex']
    assert death['at'][0] == death['vat'][0] == 'core-precinct', death
    assert 'w11' in evening['walls']['Vex'], evening['walls']['Vex']


def test_every_caller_logs_out(evening):
    assert evening['left'] == {'Noor': True, 'Ilse': True, 'Tam': True, 'Vex': True}


def test_each_wall_line_is_heard_once_and_the_cap_holds(evening):
    for name, walls in evening['walls'].items():
        assert len(walls) == len(set(walls)) and len(walls) <= 3, (name, walls)


# --- along the way ------------------------------------------------------------

def test_no_hint_on_the_path_is_cut_in_the_default_renderer_at_either_size(evening):
    assert evening['cut_hints'] == []


def test_a_toast_cut_in_the_hint_row_is_kept_whole_in_the_log(evening):
    # Presentation lead: a whole-word cut for hint-row toasts is a re-rank
    # item; P12 keeps the whole line in the log meanwhile.
    for name, kind, width, step, toast in evening['cut_toasts']:
        assert toast in evening['logs'][name], (name, step, toast)


def test_whatever_e_acts_on_is_named(evening):
    assert evening['unnamed_focus'] == []


def test_nearby_rows_are_named(evening):
    assert evening['unnamed_nearby'] == []


# --- the board (technical lead, N1) -----------------------------------------

@pytest.mark.parametrize('seed', range(8))
def test_vespers_first_offer_is_always_a_job_a_fresh_character_can_take(seed):
    """Not one lucky seed: over a spread of origins and 30-minute epochs, the
    board's first offer is one of Vesper's own grade-0 jobs with no unlock
    conditions, so a fresh character after her first job is never stuck."""
    content = load_content(ROOT/'src/karst41/content')
    rng = Random(seed)
    game = HeadlessServer(content, character_state={})
    engine = game.core.contracts.state.engine
    engine.origin = f'origin-{rng.randrange(1 << 30)}'  # The board's seed is origin and epoch.
    base = game.core.contracts.now()
    for _ in range(12):
        engine.boards.clear()
        when = base+rng.randrange(0, 90*24*3600)
        offer = engine.board('vesper', when)[0]
        template = engine.templates[offer.template]
        assert template.giver == 'vesper' and template.min_grade == 0, offer.template
        assert not template.story and not template.service and not template.unlock_conditions, offer.template
