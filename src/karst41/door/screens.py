"""Local connection/help screens; no game state or game rules."""

from typing import Any


def title(motd: str = "") -> dict[str, Any]:
    return {"screen": "title", "title": "KARST 41", "lines": ["", "The city was built to keep something in.",
        "Nobody remembers what.", "", "a real-time door for NetBBS . the year of the Exhale", "", motd,
        "", "? help    Ctrl-X leave"], "page": 1, "pages": 1}


def first_run() -> dict[str, Any]:
    return {"title": "WHAT THIS IS", "lines": [
        "You wake in Karst with a lease and no memories.",
        "Ninety days end the lease. Paid work can end it sooner.",
        "Other callers move through the same streets in real time.", "",
        "Fix a pump. Sell the record. Open a route for someone else.",
        "Street hardware and the Lattice control the same city.",
        "Read the safety label before you cross a boundary.",
        "Safe streets protect you. The outer rings do not.", "",
        "Death keeps your identity, skills and secured gear.",
        "Loose gear and carried chits can be lost. Debt can follow.",
        "Open your journal for objectives, costs and escape routes.",
        "You need no special words to use a terminal."], "page": 1, "pages": 1}


KEY_NAMES = {'up': '↑', 'down': '↓', 'left': '←', 'right': '→', 'tab': 'Tab', 'escape': 'Esc'}
# Terminal UI 10: the keys in use in a zone, in this order, with the action
# labels the keymap itself declares (walk and run name the four moves).
ZONE_KEYS = ('target_next', 'fire', 'interact', 'jack', 'map', 'character', 'contracts', 'scrollback',
             'help', 'leave')
def lease_line(row: str) -> str:
    """Terminal UI 10 (#265): the legend names the caller's own lease mark.

    `row` is the 2.4 lease row (`day n/90`, `sig n/90`, `on the roll`), so
    the precedence is the row's; each state has its canon meaning.
    """
    if row == 'on the roll':
        return 'on the roll  Your name is on the civic roll'
    if row.startswith('sig '):
        return f'{row}  Signatory: you may rent and join'
    day = row.split()[-1].split('/')[0] if row.startswith('day ') else '1'
    return f'{row}  Under lease: day {day} of 90. No renting or joining yet'


def key_name(key: str) -> str:
    if key.startswith('ctrl-'):
        return 'Ctrl-' + key[5:].upper()
    if key.startswith('shift-'):
        return 'Shift-' + key_name(key[6:])
    return KEY_NAMES.get(key, key)


def this_screen(legend: list[tuple[str, str]], keymap: dict[str, Any], keymap_id: str,
                width: int, rows: int, lease: str = '') -> tuple[list[str], list[str]]:
    """Help's first page in a zone (terminal UI 10): keys, then the legend.

    Returns page 1 (exactly `rows` lines, so the next page starts clean)
    and, when the legend overflowed, the full legend for page 2. Page 1
    never scrolls: it keeps the nearest shapes and ends with `+n more`.
    """
    table = next((t for t in keymap.get('keymaps', []) if t.get('id') == keymap_id), {})
    bindings, labels = table.get('bindings', {}), keymap.get('actions', {})
    def first(action: str) -> str:
        keys = bindings.get(action) or []
        if not keys:
            return ''
        key = keys[0]
        # A letter bound in both cases reads as the capital, like the hint
        # row (`F fire`); a case-sensitive one (vi `k`, `K`) keeps its case.
        if len(key) == 1 and key.lower() in keys and key.upper() in keys:
            return key.upper()
        return key_name(key)
    groups = []
    walk = ''.join(first(a) for a in ('move_up', 'move_down', 'move_left', 'move_right'))
    if walk:
        groups.append(walk + ' walk')
    run = ''.join(first(a) for a in ('sprint_up', 'sprint_down', 'sprint_left', 'sprint_right'))
    if run:
        groups.append((run if not run.startswith('Shift-') else 'Shift-arrows') + ' run')
    groups += [first(a) + ' ' + labels.get(a, {}).get('label', a) for a in ZONE_KEYS if first(a)]
    keys: list[str] = ['']
    for group in groups:
        joined = (keys[-1] + '  ' + group).strip()
        if len(joined) <= width or not keys[-1]:
            keys[-1] = joined
        else:
            keys.append(group)
    entries = [f'{glyph}  {word}' for glyph, word in legend]
    head = ['THIS SCREEN', *keys, '', 'In view']
    tail = ['', lease] if lease else []
    room = max(0, rows - len(head) - len(tail))
    overflow = len(entries) > room
    shown = entries[:max(0, room - 1)] + [f'+{len(entries) - max(0, room - 1)} more'] if overflow else entries
    page = (head + shown + tail)[:rows]
    page += [''] * (rows - len(page))
    rest = ['IN VIEW (all)', *entries, ''] if overflow else []
    return page, rest


def help_pages(keymap: dict[str, Any]) -> list[str]:
    lines = ["Ctrl-X then Y leaves. Ctrl-L redraws. Esc/B returns.",
             "Menus show actions first. Select a row, then its action key.",
             "A menu never pauses the city.", "",
             "Arrows move; vi hjkl/yubn moves with the vi keymap.",
             "Vi j moves down. Ctrl-O jacks. Ctrl-F opens faction chat.",
             "Ctrl-P opens scrollback. Printable vi keys type in prompts.", ""]
    for mapping in keymap.get("keymaps", []):
        lines.append(str(mapping.get("label", mapping.get("id", "Keys"))))
        for action, keys in mapping.get("bindings", {}).items():
            label = keymap.get("actions", {}).get(action, {}).get("label", action)
            lines.append(f"{', '.join(keys)}  {label}")
        lines.append("")
    return lines


class SessionWarnings:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.sent: set[int] = set()

    def update(self, elapsed: float) -> list[str]:
        result = []
        for remaining in (300, 60):  # Architecture §8.2.
            if self.limit >= remaining and elapsed >= self.limit - remaining and remaining not in self.sent:
                self.sent.add(remaining)
                result.append(f"Your BBS session ends in {remaining // 60} minute(s).")
        return result
