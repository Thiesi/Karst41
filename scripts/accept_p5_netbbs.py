"""P5 switched-on play through NetBBS Telnet -> native stdio, ordinary keys.

Use --show on an attached terminal. No position, inventory, funds, NPC,
clock or rule fixture. Captures and checks grant no creative/human gate.
"""

import argparse
import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess

from typing import Any

import accept_s10_netbbs
from accept_s10_netbbs import Connection, NativeProbe, wait
from karst41.door.palette import Palette
from karst41.door.present.paint import Paint, light_distance
from tests.presentation.snapshots import snapshot


class RecordingCaller(accept_s10_netbbs.CombatCaller):
    """S48c1b (technical lead): the packet stores its own inputs.

    Every byte string the caller writes after connect is kept in order, so
    a later change can be checked against the stored inputs instead of
    being reconstructed from the script. Only this process uses it."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.inputs: list[Any] = []
        send = self.write

        async def write(data: bytes) -> None:
            self.inputs.append(data.decode('utf-8'))
            await send(data)
        self.write = write


accept_s10_netbbs.CombatCaller = RecordingCaller
_return_to = accept_s10_netbbs.return_to


def _recorded_return_to(service: Any, session: Any, home: Any) -> None:
    # create()'s disclosed server-side return to the arrival tile is a move,
    # not a key; record it in the same sequence so the inputs are complete.
    caller = next(c.caller for c in _probes[-1].connections if c.session is session)
    caller.inputs.append({'move': 'return_to', 'tile': list(home)})
    _return_to(service, session, home)


accept_s10_netbbs.return_to = _recorded_return_to
_probes: list['PresentationProbe'] = []


class PresentationProbe(NativeProbe):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.steps: dict[int, list[dict[str, Any]]] = {}
        self.cursor: dict[int, int] = {}
        _probes.append(self)

    def save(self, connection: Connection, label: str) -> None:
        super().save(connection, label)
        (self.root / (label + '.json')).write_text(json.dumps(snapshot(connection.caller.screen),
            ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8', newline='\n')
        inputs = connection.caller.inputs
        start = self.cursor.get(id(connection.caller), 0)
        self.steps.setdefault(id(connection.caller), []).append({'capture': label, 'inputs': inputs[start:]})
        self.cursor[id(connection.caller)] = len(inputs)


async def accept(root: Path, door_python: Path, show: bool,
                 sizes: tuple[tuple[int, int], ...], tiers: tuple[str, ...]) -> None:
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    assert not subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip()
    console = {'isatty': os.isatty(1), 'size': None}
    if show:
        assert console['isatty'], '--show requires an attached terminal'
        terminal = os.get_terminal_size(1)
        console['size'] = [terminal.columns, terminal.lines]
        if len(sizes) == 1:
            assert tuple(console['size']) == sizes[0], 'Resize the attached terminal to --size'
    root.mkdir(parents=True, exist_ok=False)
    probe = PresentationProbe(root, door_python, show,
                              door_environment={'DOOR_KARST41_PRESENTATION': '1'}, show_all_tiers=True)
    await probe.start()
    cases = []
    try:
        for width, height in sizes:
            for tier in tiers:
                # Released NetBBS usernames/door handles are ASCII-only. Use
                # ordinary character creation for genuine live wide-name data.
                # At 132x50 the combined wide name + ASCII handle fits the
                # pinned name row in full, as requested in #106's handoff.
                label = {'16': 'P', '256': 'Q', 'truecolor': 'R'}[tier]
                # S48c1b: the training drone wakes only for an unread body, so
                # the caller fights it first and reads the record after; "done"
                # is held and applied at creation (checked below).
                caller = await probe.open(width, height, label, archetype=0, sixteen=tier == '16',
                                          truecolor=tier == 'truecolor', unread=True)
                assert caller.session.caps['tier'] == tier
                assert probe.game.unread(caller.session.player_id)
                display = caller.caller.screen
                captures = []

                async def settled() -> None:
                    await wait(lambda: caller.session.ack_revision == caller.session.view_revision)

                def save(step: str) -> None:
                    label = f'{step}-{width}x{height}-{tier}'
                    probe.save(caller, label)
                    captures.append(label)

                await settled()
                assert display.cell(0, 0) == '╔'  # New safe frame proves launch switch reached the wheel.
                await probe.walk(caller, (21, 14))
                await wait(lambda: any(a.owner == caller.avatar.actor.id and a.behaviour.get('training')
                                      for a in probe.service.world.actors.values()))
                drone = next(a for a in probe.service.world.actors.values()
                             if a.owner == caller.avatar.actor.id and a.behaviour.get('training'))
                await probe.target(caller, drone.id)
                # TARGET and HP labels also exist with no selected target.
                # Wait for the actual name in TARGET as well as NEARBY.
                await wait(lambda: display.screen_text().count('Training drone') >= 2)
                revision = caller.session.view_revision
                await caller.caller.write(b'f')
                await wait(lambda: caller.session.ack_revision > revision
                           and display.screen_text().count('Training drone') >= 2)
                save('training-drone')
                async with asyncio.timeout(45):
                    while probe.game.players[caller.session.player_id].progress.get('wake_combat') != 'done':
                        await caller.caller.write(b'f')
                        await asyncio.sleep(.2)
                await settled()
                save('training-complete')
                await probe.create(caller, width, height, label, 0, tier == '16', capture_wake=True)
                captures.append(f'wake-record-{width}x{height}-{tier}')
                player = probe.game.players[caller.session.player_id]
                name = player.name
                assert '界' in name and player.progress.get('wake_combat') == 'done'
                await settled()
                assert display.cell(0, 0) == '╔'
                save('wake-hall')
                await probe.travel(caller, 'vatside-clinics')
                await probe.travel(caller, 'core-plaza')
                # Walk beside the shipped patrol route, rather than capture
                # only the already-bright neutral NPC near the clinic exit.
                # This changes ordinary inputs, never actors or world state.
                await probe.walk(caller, (31, 5))
                await settled()
                assert 'Meridian Plaza' in display.screen_text().splitlines()[0]
                person_lift = None
                if tier == 'truecolor':
                    data = json.loads((Path(__file__).resolve().parents[1] /
                        'src/karst41/content/palette.json').read_text(encoding='utf-8'))
                    palette = Palette(data, tier)
                    paint = Paint(palette)
                    original, lifted = palette.style('npc_wardens').fg, paint('npc_wardens').fg
                    assert original != lifted

                    def lifted_people() -> list[tuple[int, int]]:
                        return [(x, y) for y, row in enumerate(display.grid) for x, glyph in enumerate(row)
                                if glyph == '☻' and display.renditions.get((x, y)) is not None
                                and display.renditions[x, y].foreground == (2, *lifted)]

                    await wait(lambda: bool(lifted_people()))
                    person_lift = {'role': 'npc_wardens', 'original_rgb': original, 'paint_rgb': lifted,
                        'original_distance': light_distance(original, paint.bands.ground),
                        'paint_distance': light_distance(lifted, paint.bands.ground),
                        'people_floor': paint.bands.people, 'terminal_cells': lifted_people()}
                save('meridian-plaza')
                for key, title, step in ((b'i', 'INVENTORY', 'inventory'),
                                         (b'q', 'CONTRACTS', 'contracts'),
                                         (b'?', 'HELP', 'help')):
                    await caller.caller.write(key)
                    await wait(lambda: title in display.screen_text())
                    await settled()
                    save(step)
                    await caller.caller.write(b'\x1b')
                    await wait(lambda: 'Meridian Plaza' in display.screen_text().splitlines()[0])
                    await settled()
                cases.append({'size': [width, height], 'tier': tier,
                              'handle': caller.session.door_info['handle'],
                              'character_name': name, 'wide_bbs_handle': False, 'wide_character_name': True,
                              'new_safe_frame': True, 'training_fight': 'done',
                               'plaza_person_lift': person_lift,
                              'captures': captures, 'ordinary_inputs': True,
                              'inputs': {'connect': {'naws': [width, height],
                                                     'term': 'xterm-direct' if tier == 'truecolor' else 'xterm-256color',
                                                     'colorterm': 'truecolor' if tier == 'truecolor' else None,
                                                     'sixteen_account': tier == '16'},
                                         'steps': probe.steps[id(caller.caller)]}})
                await probe.leave(caller, f'finished-{width}x{height}-{tier}')
                probe.report(f'PASS P5 {width}x{height} {tier}: Wake, training, Plaza, inventory, contracts, help')
        assert not probe.errors, probe.errors
    finally:
        await probe.close()
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip() == head
    assert not subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip()
    manifest = {path.name: {'bytes': path.stat().st_size, 'sha256': sha256(path.read_bytes()).hexdigest()}
                for case in cases for label in case['captures']
                for ext in ('.ans', '.txt', '.json') for path in [root / (label + ext)]}
    (root / 'result.json').write_text(json.dumps({'head': head, 'passed': True, 'cases': cases,
        'inputs_record': {'script': 'scripts/accept_p5_netbbs.py', 'commit': head,
                          'form': 'per case: connect (Telnet NAWS/TERM/COLORTERM), then per capture every '
                                  'string the caller wrote since the previous capture, in order, plus '
                                  'server-side moves as {"move": ...}'},
        'transport': 'NetBBS Telnet -> registered native stdio, DOOR_KARST41_PRESENTATION=1',
        'source_matched_wheel': json.loads((root / 'native-client.json').read_text()),
        'show': show, 'console': console, 'human_approval': False, 'fixtures': 'private node/world/accounts only; normal game rules',
        'manifest': manifest}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps({'passed': True, 'cases': len(cases)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--door-python', type=Path, required=True)
    parser.add_argument('--show', action='store_true')
    parser.add_argument('--size', choices=['80x24', '132x50'])
    parser.add_argument('--tier', choices=['16', '256', 'truecolor'], help='Diagnostic subset; default runs all tiers')
    args = parser.parse_args()
    sizes = (tuple(map(int, args.size.split('x'))),) if args.size else ((80, 24), (132, 50))
    tiers = (args.tier,) if args.tier else ('16', '256', 'truecolor')
    asyncio.run(accept(args.root.resolve(), args.door_python, args.show, sizes, tiers))
