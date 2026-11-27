"""P2: unchanged chrome through a real native door with wide caller names.

Use a source-matched installed wheel and the NetBBS interpreter. Ordinary
character creation, inventory paging, help and scrollback use real keys.
No placement, funding, rule, clock or composed-screen fixtures are used.
"""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
from typing import Any

from accept_s10_netbbs import Connection, NativeProbe, wait
from acceptance_settle import settled as settle, settled_now
from tests.presentation.snapshots import snapshot


async def save(probe: NativeProbe, caller: Connection, label: str) -> None:
    await settle(caller.session, caller.caller)  # #255: acknowledged and the last frame whole.
    probe.save(caller, label)
    (probe.root/(label+'.json')).write_text(json.dumps(snapshot(caller.caller.screen),
        ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')


async def accept(root: Path, door_python: Path, show: bool) -> None:
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    assert not subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip()
    root.mkdir(parents=True, exist_ok=False)
    probe = NativeProbe(root, door_python, show)
    await probe.start()
    cases: list[dict[str, Any]] = []
    try:
        for width, height in ((80, 24), (132, 50)):
            caller = await probe.open(width, height, 'P2', sixteen=True)
            screen = caller.caller.screen
            name = f'P2界{width}'
            await wait(lambda: name in screen.screen_text()
                       and settled_now(caller.session, caller.caller))
            assert (caller.session.caps['width'], caller.session.caps['height'], caller.session.caps['tier']) == (width, height, '16')
            labels = [f'wake-{width}x{height}']
            await save(probe, caller, labels[-1])
            await caller.caller.write(b'i')
            await wait(lambda: 'INVENTORY' in screen.screen_text()
                       and settled_now(caller.session, caller.caller))
            for key in (b'\x1b[6~', b'\x1b[5~'):
                revision = caller.session.view_revision
                await caller.caller.write(key)
                await wait(lambda: caller.session.view_revision > revision
                           and settled_now(caller.session, caller.caller))
            labels.append(f'inventory-{width}x{height}')
            await save(probe, caller, labels[-1])
            await caller.caller.write(b'\x1b')
            await wait(lambda: 'Wake Hall' in screen.screen_text().splitlines()[0])
            await caller.caller.write(b'?')
            await wait(lambda: 'HELP' in screen.screen_text())
            await caller.caller.write(b'\x1b[6~')
            await wait(lambda: 'Page 2/' in screen.screen_text())
            labels.append(f'help-{width}x{height}')
            await save(probe, caller, labels[-1])
            await caller.caller.write(b'\x1b')
            await wait(lambda: 'Wake Hall' in screen.screen_text().splitlines()[0])
            await caller.caller.write(b'\x10')
            await wait(lambda: 'SCROLLBACK' in screen.screen_text())
            labels.append(f'scrollback-{width}x{height}')
            await save(probe, caller, labels[-1])
            await caller.caller.write(b'\x1b')
            await wait(lambda: 'Wake Hall' in screen.screen_text().splitlines()[0])
            await probe.leave(caller, f'finished-{width}x{height}')
            cases.append({'size': [width, height], 'name': name, 'tier': '16',
                'captures': labels, 'inventory_page_keys': True, 'help_page_2': True,
                'scrollback': True, 'clean_exit': True})
        assert not probe.errors, probe.errors
    finally:
        await probe.close()
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip() == head
    assert not subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip()
    client = json.loads((root/'native-client.json').read_text())
    result = {'head': head, 'dirty': False, 'passed': True, 'cases': cases,
        'transport': 'NetBBS Telnet -> registered native stdio',
        'native_python_tree_sha256': client['python_tree_sha256'],
        'fixtures': 'none beyond private node/world and ordinary new characters',
        'human_approval': False, 'performance_gate': 'not asserted; full native matrix required'}
    (root/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n',
        encoding='utf-8', newline='\n')
    print(json.dumps({'passed': True, 'cases': len(cases), 'captures': 8}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--door-python', type=Path, required=True)
    parser.add_argument('--show', action='store_true')
    args = parser.parse_args()
    asyncio.run(accept(args.root.resolve(), args.door_python, args.show))
