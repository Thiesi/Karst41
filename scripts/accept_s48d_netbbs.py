"""S48d: a voice in the walls (native NetBBS evidence).

One renderer per run (legacy default door views, or the opt-in
presentation), each colour tier in turn, over NetBBS Telnet to the
registered native stdio door. Real keys throughout.

Two callers per tier, each in a state where a wall line may speak (technical
lead): a created character with no live opening runner. Each is made with
ordinary keys (the opening, the record, a name), walks out of the hall to
Clinic Row and drops the line. A detach after the record, outside the hall,
ends the runner for any caller (S48c2, technical lead's fix 2). The caller
then dials back in. Nothing bypasses the silence rule.

1. The hall: back into the Wake Hall, to the first room's west wall beside
   the painted line, and three real steps into it: W02. An orderly's bark
   follows in the same log (speech with a speaker, against the voice's
   unattributed `·`).
2. The Row: from Core Plaza onto the Row, along the street to the Tin
   Halo's door: W18 and its answer, then the Row's own zone ambience in the
   same log.

Declared fixtures:
- One Sablier orderly is relocated beside the caller at the hall's west
  wall and holds still, and its bark is made due once the caller's budget
  has reopened (the P8 precedent), instead of waiting its 90 s interval.
- The Row's ambience is made due once the budget has reopened after W18
  (the P8 precedent), instead of its 60-90 s cadence.
- The Row walk goes by two waypoints, (40, 22) and (88, 22), so it crosses
  no other line's region on the way and the evening's three lines are W14,
  W18 and nothing else. A caller walking another way hears other lines; the
  rules are the same.
"""

import argparse
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import traceback
from typing import Any

from accept_s10_netbbs import CombatCaller, Connection, NativeProbe, wait
from netbbs.db.users import get_user_by_id
from karst41.server import ambience as amb
from karst41.server.clock import TICK_HZ
from tests.presentation.snapshots import snapshot

ROW, HALL, CLINICS, PLAZA = 'sodium-row', 'vatside-wake-hall', 'vatside-clinics', 'core-plaza'
WEST_WALL, ORDERLY_AT = (1, 8), (2, 9)
ROW_ROUTE, HALO = ((40, 22), (88, 22)), (90, 26)
LEFT = b'\x1b[D'


async def redial(probe: NativeProbe, previous: Connection, width: int, height: int, sixteen: bool,
                 truecolor: bool) -> Connection:
    """Hang up, then dial back in as the same user (the S18 reconnect, any zone, any tier)."""
    pid = previous.session.player_id
    previous.writer.close()
    await previous.writer.wait_closed()
    previous.task.cancel()
    await wait(lambda: all(s.player_id != pid for s in probe.game.sessions.active.values()), 15)
    user = get_user_by_id(probe.db, int(previous.session.door_info['user_id']))
    probe.accounts.append((user, sixteen))
    reader, writer = await asyncio.open_connection('127.0.0.1', probe.node.port)

    async def send(data: bytes) -> None:
        writer.write(data)
        await writer.drain()
    writer.write(b'\xff\xfb\x1f\xff\xfa\x1f'+width.to_bytes(2, 'big')+height.to_bytes(2, 'big')+b'\xff\xf0')
    term = b'xterm-direct' if truecolor else b'xterm-256color'
    writer.write(b'\xff\xfb\x18\xff\xfa\x18\x00'+term+b'\xff\xf0')
    if truecolor:
        writer.write(b'\xff\xfb\x27\xff\xfa\x27\x00\x00COLORTERM\x01truecolor\xff\xf0')
    await writer.drain()
    caller = CombatCaller(lambda: reader.read(65536), send, width, height, probe.show and (sixteen or probe.show_all_tiers))
    caller.display_write = probe.display_write
    await caller.expect('Available doors')
    await caller.write(b'\x1b[B\r')
    name = probe.service.world.zones[CLINICS].template.meta.name
    await caller.until(lambda: name in caller.screen.screen_text().splitlines()[0], timeout=30)

    async def pump() -> None:
        while chunk := await reader.read(65536):
            caller.output.extend(chunk)
            caller.replay()
    task = asyncio.create_task(pump())
    session = next(s for s in probe.game.sessions.active.values() if s.player_id == pid)
    result = Connection(caller, writer, task, session, probe.game.sim.avatars[session.user_id])
    probe.connections.append(result)
    return result


async def accept(root: Path, door_python: Path, width: int, height: int, modern: bool, show: bool) -> None:
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    assert not subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip()
    root.mkdir(parents=True, exist_ok=False)
    renderer = 'present' if modern else 'legacy'
    probe = NativeProbe(root, door_python, show, door_environment={
        'DOOR_KARST41_PRESENTATION': '1' if modern else '0'}, show_all_tiers=True)
    await probe.start()
    rows: list[dict[str, Any]] = []
    try:
        core = probe.service.core
        clock = probe.service.clock
        content = probe.service.world.content
        voice_of = core.dialogue.ambience
        lines = {ln.id: ln for ln in content.walls.lines}
        assert content.walls.enabled, 'the wall voice is switched off'
        for tier in ('16', '256', 'truecolor'):
            kwargs = {'sixteen': tier == '16', 'truecolor': tier == 'truecolor'}
            for scene in ('hall', 'row'):
                caller = await probe.open(width, height, scene.title()+tier, **kwargs)
                assert caller.session.caps['tier'] == tier, caller.session.caps
                pid = caller.session.player_id
                await probe.travel(caller, CLINICS)
                caller = await redial(probe, caller, width, height, **kwargs)
                # Technical lead: never mid-opening and never an unread body.
                assert not probe.game.opening_live(pid) and not probe.game.unread(pid)
                assert probe.game.players[pid].progress['opening']['done']
                heard: list[tuple[int, str, str, str]] = []
                send = caller.session.send

                def listen(frame: Any, send: Any = send, heard: list = heard) -> bool:
                    # Observation only: note every log line delivered.
                    if frame.type == 'log' and not frame.data.get('replay'):
                        heard.extend((clock.now_tick(), line['kind'], line.get('role', ''), line['text'])
                                     for line in frame.data['lines'])
                    return send(frame)
                caller.session.send = listen

                def screen() -> str:
                    return caller.caller.screen.screen_text()

                def shows(text: str) -> bool:
                    """The whole entry is drawn in the log (P12: whole entries)."""
                    entries: list[str] = []
                    for row in screen().splitlines():
                        cells = row[1:-1].rstrip()
                        if cells.startswith('  ') and entries:
                            entries[-1] += ' ' + cells.strip()
                        elif cells:
                            entries.append(cells)
                    return any(text in entry for entry in entries)

                def said(lid: str) -> bool:
                    return any(text == lines[lid].text for _, _, _, text in heard)

                async def delivered() -> None:
                    await wait(lambda: caller.session.ack_revision == caller.session.view_revision)
                    await wait(lambda: caller.caller.output.endswith((b'\x1b[?25l', b'\x1b[?25h')))

                async def budget_open() -> None:
                    await wait(lambda: caller.session.voice_tick is None or (
                        clock.now_tick() - caller.session.voice_tick >= amb.LINE_BUDGET_SECONDS * TICK_HZ),
                        amb.LINE_BUDGET_SECONDS + 5)

                def save(name: str, facts: dict[str, Any]) -> None:
                    label = f's48d-{name}-{width}x{height}-{tier}-{renderer}'
                    probe.save(caller, label)
                    (root/(label+'.json')).write_text(json.dumps(snapshot(caller.caller.screen),
                        ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
                    rows.append({'capture': label, 'scene': name, 'size': [width, height], 'tier': tier,
                                 'renderer': renderer, **facts,
                                 'heard': [{'second': round(t / TICK_HZ, 1), 'kind': k, 'role': r, 'text': x}
                                           for t, k, r, x in heard if k in ('ambient', 'speech')],
                                 'sha256': {suffix: sha256((root/(label+suffix)).read_bytes()).hexdigest()
                                            for suffix in ('.ans', '.txt', '.json')}})

                if scene == 'hall':
                    await probe.travel(caller, HALL)
                    await probe.walk(caller, WEST_WALL)
                    me = caller.avatar.actor
                    zone = probe.service.world.zones[HALL]
                    orderly = min((a for a in zone.actors.values()
                                   if a.behaviour.get('template') == 'npc-sablier-orderly'),
                                  key=lambda a: ((a.x-me.x)**2+(a.y-me.y)**2, a.id))
                    probe.service.world.enter(orderly, HALL, probe.service.world.free_tile(HALL, ORDERLY_AT, orderly))
                    orderly.behaviour.update(home=orderly.position, pause=1 << 40)
                    await budget_open()
                    ear = core.walls.ears[caller.session.id]
                    for n in range(3):  # Three real steps into the west wall.
                        before = ear.bumps
                        await caller.caller.write(LEFT)
                        await wait(lambda: ear.bumps > before or said('w02'))
                    await wait(lambda: said('w02'))
                    await wait(lambda: shows(lines['w02'].text))
                    # The orderly's bark in the same log (declared: made due).
                    await budget_open()
                    voice = voice_of.zones[HALL]
                    mark = len(heard)
                    voice.barks.next_due[orderly.id] = 0
                    await wait(lambda: any(k == 'speech' for _, k, _, _ in heard[mark:]))
                    bark = next(x for _, k, _, x in heard[mark:] if k == 'speech')
                    await wait(lambda: shows(bark) and shows(lines['w02'].text))
                    await delivered()
                    save('hall-wall-line', {'line': 'w02', 'text': lines['w02'].text, 'bark': bark,
                                            'player': list(me.position), 'orderly': list(orderly.position)})
                else:
                    await probe.travel(caller, PLAZA)
                    await probe.travel(caller, ROW)
                    await wait(lambda: said('w14'), 35)
                    for point in (*ROW_ROUTE, HALO):
                        await probe.walk(caller, point)
                    await wait(lambda: said('w18'), amb.LINE_BUDGET_SECONDS + 5)
                    pair = (lines['w18'].text, lines['w18'].answer)
                    await wait(lambda: shows(pair[0]) and shows(pair[1]))
                    texts = [x for _, _, _, x in heard]
                    assert texts[texts.index(pair[0]) + 1] == pair[1]  # Adjacent, in one frame.
                    # The Row's own ambience in the same log (declared: made due).
                    await budget_open()
                    voice = voice_of.zones[ROW]
                    mark = len(heard)
                    voice.ambient_due = clock.now_tick()
                    await wait(lambda: any(k == 'ambient' and x in content.looks[ROW].ambient
                                           for _, k, _, x in heard[mark:]))
                    ambient = next(x for _, k, _, x in heard[mark:] if k == 'ambient')
                    await wait(lambda: shows(ambient) and shows(pair[0]) and shows(pair[1]))
                    await delivered()
                    save('row-wall-pair', {'line': 'w18', 'text': pair[0], 'answer': pair[1], 'ambience': ambient,
                                           'player': list(caller.avatar.actor.position)})
                wall = [(r, x) for _, k, r, x in heard
                        if k == 'ambient' and any(x in (ln.text, ln.answer) for ln in lines.values())]
                assert all(r == 'log_ambient' for r, _ in wall), wall
                assert caller.session.walls_heard <= content.walls.per_evening
                await probe.leave(caller, f's48d-left-{scene}-{width}x{height}-{tier}-{renderer}')
        assert not probe.errors and probe.service.failure is None
        (root/'s48d-results.json').write_text(json.dumps({
            'passed': True, 'head': head, 'transport': 'NetBBS Telnet -> registered native stdio',
            'renderer': renderer, 'size': [width, height], 'captures': rows,
            'numbers': {'per_evening': content.walls.per_evening, 'line_budget_seconds': amb.LINE_BUDGET_SECONDS,
                        'danger_hold_seconds': amb.DANGER_HOLD_SECONDS},
            'state': 'created characters; the opening ended by a detach outside the hall after the record '
                     '(S48c2 fix 2); no runner live and no unread body at any capture',
            'fixtures': ['one Sablier orderly relocated beside the caller at the west wall, holding still; '
                         'its bark made due once the budget reopened (P8 precedent)',
                         'the Row ambience made due once the budget reopened after W18 (P8 precedent)',
                         'the Row walk goes by (40, 22) and (88, 22), crossing no other line\'s region'],
            'presentation_acceptance': False, 'h1_layout_approval': False,
            's25_layout_approval': False}, indent=2)+'\n', encoding='utf-8', newline='\n')
        probe.report(f'S48d PASS {width}x{height}/{renderer}: wall line in the hall, pair on the Row')
    except BaseException:
        (root/'failure-trace.txt').write_text(traceback.format_exc(), encoding='utf-8')
        raise
    finally:
        await probe.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--door-python', type=Path, required=True)
    parser.add_argument('--size', choices=('80x24', '132x50'), required=True)
    parser.add_argument('--presentation', choices=('legacy', 'modern'), required=True)
    parser.add_argument('--show', action='store_true')
    args = parser.parse_args()
    width, height = map(int, args.size.split('x'))
    asyncio.run(accept(args.root, args.door_python, width, height, args.presentation == 'modern', args.show))
