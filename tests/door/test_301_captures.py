"""#301 acceptance captures through the presentation door (presentation lead, 7232531002).

The runner's gate-refused captures show the legacy renderer, where a toast
goes on the divider. These frames show the default presentation renderer,
headless, through the production Core and Display(presentation=True), at
80x24 and 132x50 in the 256-colour tier, plus 80x24 in 16 colours, with the
shortened wording (7232688385). One legacy frame (presentation=False) shows
the same wording on the divider, without another runner pass.

The presentation door places a toast by an existing rule (Terminal UI 2.5,
`Display._notice_to_header`): while a danger or combat line is under its
protection window (PROTECT_SECONDS), or the caller has hazards or a hostile
target, the toast goes to the header so the hint row can't hide the danger;
otherwise it is the hint row's primary field, followed by the back and escape
groups. #301 changes neither placement. So two refusals:

- **Hornets** (the runner's refusal): the room menu is opened while the
  Hornets warn; their own phase turns to attacking while the Enter is in
  flight; the Enter arrives with the revision it was made on and is refused.
  The Hornets' volleys are fresh combat lines, so the toast is in the header.
- **Hall doors**, no danger: another runner flips the Civic Ledger's hall
  doors under the caller's open room menu (the in-process test in
  tests/server/test_301_refused_choice_toast.py). The toast is the hint row's
  primary field, whole beside the room menu's own back and escape groups.

In both the menu stays open on the new terms and nothing is chosen.

Test content only: the Hornets case moves Hornets into the Civic Ledger's
entry room, so a runner on the plaza reaches them in one step. No stock core
with Hornets has a public entrance, and S14's Gate Nine fixture is a
NetBBS-only rewrite.
"""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from karst41.content.loader import load_content
from karst41.door.model import Display
from karst41.server.clock import TICK_HZ
from karst41.server.protocol import Frame
from karst41.server.sessions import STALE_CHOICE_TOAST
from tests.character_helpers import create, place
from tests.fake_terminal import FakeTerminal
from tests.harness import HeadlessServer
from tests.lattice.test_encounters import enter
from tests.lattice.test_engine import intent
from tests.lattice.test_interlock import control_room, flip
from tests.presentation.snapshots import assert_golden

GOLDEN = Path('tests/presentation/golden/s301')
CORE = 'lat-civic-ledger'
SIZES = [(80, 24, '256'), (132, 50, '256'), (80, 24, '16')]


class Caller:
    """One client, and every frame its door has received."""

    def __init__(self, game: HeadlessServer, client: Any) -> None:
        self.game, self.client, self.frames = game, client, list(game.frames_for(client))

    def drain(self) -> list[Frame]:
        new = self.game.frames_for(self.client)
        self.frames += new
        return new


def scene(repo_root: Path, width: int, height: int, hornets: bool, callers: int = 1) -> tuple[HeadlessServer, list[Caller]]:
    content = load_content(repo_root/'src/karst41/content')
    content.zones = {k: replace(z, meta=replace(z.meta, spawners=())) for k, z in content.zones.items()}
    if hornets:
        core = content.cores[CORE]
        content.cores[CORE] = replace(core, rooms=tuple(
            replace(room, ice=('ice_hornets',) if room.id == core.entry else ()) for room in core.rooms))
    game = HeadlessServer(content, character_state={})
    clients = []
    for uid in range(1, callers+1):
        client = game.connect({'user_id': uid, 'handle': f'Picker{uid}', 'node_name': 'S301'},
                              width=width, height=height)
        create(game, client, archetype=0, name=f'Picker{uid}', card='none')
        place(game, client, 'core-plaza', (29+uid, 5))
        clients.append(client)
    return game, [Caller(game, client) for client in clients]


def menu_row(game: HeadlessServer, caller: Caller, row: str) -> list[str]:
    """A row of the caller's open room menu, as the server shows it now."""
    encounters = game.core.lattice.encounters
    presence = game.core.lattice.presences[caller.client.session.player_id]
    return next(r['cells'] for r in encounters.rows(presence, encounters.menus[caller.client.session.id])
                if r['id'] == row)


def open_room_menu(game: HeadlessServer, caller: Caller) -> int:
    intent(game, caller.client, 'lat.cancel')
    caller.client.send(Frame('key', {'k': 'e', 't': 0}))
    game.step(2)
    caller.drain()
    assert caller.client.session.id in game.core.lattice.encounters.menus
    return caller.client.session.view_revision  # What the door shows and its Enter carries.


def stale_enter(game: HeadlessServer, caller: Caller, row: str, seen: int) -> None:
    caller.drain()
    caller.client.send(Frame('intent', {'name': 'menu_select', 'args': {'row': row, 'key': 'enter'},
                                        'request_id': 't301-capture', 'expected_revision': seen}))
    game.step(2)
    after = caller.drain()
    assert [f.data['text'] for f in after if f.type == 'toast'] == [STALE_CHOICE_TOAST]
    assert caller.client.session.id in game.core.lattice.encounters.menus, 'The refused Enter left the menu open'


def shoot(info: Any, game: HeadlessServer, caller: Caller, width: int, height: int, tier: str,
          presentation: bool = True) -> list[str]:
    model = Display(replace(info, width=width, height=height, color_depth=tier), presentation=presentation)
    now = game.core.clock.now_tick() / TICK_HZ
    for frame in caller.frames:
        model.receive(frame, now)
    model.prompt = None  # Creation's name prompt, long answered.
    terminal = FakeTerminal(width, height)
    terminal.write(model.render(now + 0.5)[0])
    shoot.terminal = terminal
    return terminal.screen_text().splitlines()


def pin(request: Any, name: str, width: int, height: int, tier: str) -> None:
    assert_golden(GOLDEN, f's301_{name}_{width}x{height}_{tier}', shoot.terminal, tier, trim=True,
                  update=request.config.getoption('--update-goldens'))


@pytest.mark.parametrize('width,height,tier', SIZES)
def test_hornets_refusal_in_a_fight_the_toast_takes_the_header(info, repo_root, request, width, height, tier):
    game, (caller,) = scene(repo_root, width, height, hornets=True)
    lattice = game.core.lattice
    presence = enter(game, caller.client, CORE)
    hornets = next(i for i in lattice.encounters.defenders(presence) if i.template.class_ == 'hornets')
    row = 'ice:'+hornets.id
    seen = open_room_menu(game, caller)
    assert menu_row(game, caller, row)[2] == 'warning'
    for _ in range(TICK_HZ*10):  # The Enter is in flight while the Hornets turn.
        game.step()
        if menu_row(game, caller, row)[2] == 'attacking':
            break
    assert menu_row(game, caller, row)[2] == 'attacking', 'The Hornets never turned'
    stale_enter(game, caller, row, seen)
    assert presence.cast is None and presence.procedure is None, 'Nothing was chosen'
    rows = shoot(info, game, caller, width, height, tier)
    pin(request, 'hornets', width, height, tier)
    # Fresh combat lines protect the hint row (Terminal UI 2.5): the toast is
    # whole in the header, and the hint row keeps the danger's own help.
    assert STALE_CHOICE_TOAST in rows[0], rows[0]
    assert STALE_CHOICE_TOAST not in rows[-2], rows[-2]
    screen = '\n'.join(rows)
    assert 'Hornets' in screen and 'attacking' in screen and 'warning  ' not in screen


def hall_doors_refusal(repo_root: Path, width: int, height: int) -> tuple[HeadlessServer, Caller]:
    game, (flipper, caller) = scene(repo_root, width, height, hornets=False, callers=2)
    lattice = game.core.lattice
    first = enter(game, flipper.client, CORE)
    control = control_room(game, first)
    second = enter(game, caller.client, CORE)
    second.room = first.room
    row = 'control:'+control.id
    seen = open_room_menu(game, caller)
    before = menu_row(game, caller, row)
    flip(game, flipper.client)
    game.commit_commerce()
    game.step(2)
    assert menu_row(game, caller, row) != before, 'The doors flipped under the open menu'
    stale_enter(game, caller, row, seen)
    assert not second.procedure, 'Nothing was chosen'
    return game, caller


@pytest.mark.parametrize('width,height,tier', SIZES)
def test_hall_doors_refusal_without_danger_the_toast_leads_the_hint_row(info, repo_root, request, width, height, tier):
    game, caller = hall_doors_refusal(repo_root, width, height)
    rows = shoot(info, game, caller, width, height, tier)
    pin(request, 'hall_doors', width, height, tier)
    hint = rows[-2]
    # The toast is the hint row's primary field, whole, then the room menu's
    # own back and escape groups, nothing clipped (31 + 2 + 28 + 2 + 12 = 75
    # of 78 cells at 80x24).
    assert hint.strip('║').startswith(STALE_CHOICE_TOAST+'  Esc/B returns without acting  Ctrl-X leave'), hint
    assert STALE_CHOICE_TOAST not in rows[0]


def test_hall_doors_refusal_in_the_legacy_renderer_the_toast_is_on_the_divider(info, repo_root, request):
    """The runner's legacy captures with the shortened wording: the toast is
    whole on the divider (chrome.render_effects), the menu above it."""
    width, height, tier = 80, 24, '256'
    game, caller = hall_doors_refusal(repo_root, width, height)
    rows = shoot(info, game, caller, width, height, tier, presentation=False)
    pin(request, 'hall_doors_legacy', width, height, tier)
    divider = next(i for i, row in enumerate(rows) if STALE_CHOICE_TOAST in row)
    assert rows[divider].startswith('├'+STALE_CHOICE_TOAST+'─') and rows[divider].endswith('┤'), rows[divider]
    assert any('Flip hall doors' in row for row in rows[:divider])
