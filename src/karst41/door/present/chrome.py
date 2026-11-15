"""P5 chrome: V1 1060c9d adapted to P2 rectangles and P4 log kinds.

UI 2.1/2.4/2.5 and #106's 2026-10-30 decisions govern corrections.
No hint fitting, duplicate geometry, text classifier or clock reads.
"""

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any

from karst41.door.cells import CellBuffer, clean_text, display_width, truncate
from karst41.door.chrome import word_prefix, zone_cut, zone_minimum
from karst41.door.fit import LOG_PROTECT_EXTRA, log_cut, log_window, select_lazy
from karst41.door.log import wrap as word_wrap
from karst41.layout import Layout, Rect
from karst41.door.present.paint import Paint

LINES = {True: ('═', '║', '╔', '╗', '╚', '╟', '╢', '╤'),
         False: ('─', '│', '┌', '┐', '└', '├', '┤', '┬')}
GUTTERS = {'place': '»', 'ambient': '·', 'speech': '"', 'combat': '*',
           'danger': '‼', 'system': '+', 'reward': '√'}
COALESCE_SECONDS = 10.0  # Terminal UI 2.5.
REWARD_SECONDS = 5.0  # Terminal UI 2.5.
PROTECT_SECONDS = 10.0  # Terminal UI 2.5.
NOTICE_SECONDS = 8.0  # Terminal UI 2.5: minimum, plus a key after arrival.


def frame(buffer: CellBuffer, layout: Layout, paint: Paint, safety: str, *,
          side: bool = True, flash: bool = False) -> None:
    h, v, tl, tr, bl, lj, rj, tj = LINES[safety in ('safe', 'pocket')]
    style = paint('hp_critical' if flash else 'frame_' + safety, bold=flash)
    area = layout.frame
    buffer.put(area.x, area.y, tl + h * (area.width - 2) + tr, style)
    for y in range(area.y + 1, area.bottom - 1):
        buffer.put(area.x, y, v, style)
        buffer.put(area.right - 1, y, v, style)
    buffer.put(area.x, area.bottom - 1, bl + h * (area.width - 2), style)
    sep = layout.separator
    buffer.put(sep.x, sep.y, lj + '─' * (sep.width - 2) + rj, style)
    if side:
        divider = layout.divider
        buffer.put(divider.x, area.y, tj, style)
        for y in range(divider.y, divider.bottom):
            buffer.put(divider.x, y, '│', style)
        buffer.put(divider.x, sep.y, '┴', style)


def header(buffer: CellBuffer, layout: Layout, paint: Paint, left: list[tuple[str, str]],
           right: list[tuple[str, str]], *, notice: tuple[str, str, float] | None = None) -> None:
    """Terminal UI 2 (S48c3b, #261): drop whole low-priority segments, then
    cut the zone at a whole word, keeping at least its first.

    The district is left out when it names the zone. Safety and threat words
    never drop and come before the zone's minimum. A notice gets what is left
    after that minimum: the gap before it shrinks to one cell, then it is
    cut at a whole word; the full notice stays on the toast.
    """
    area = layout.header
    shown = [(clean_text(text), role) for text, role in left if text]
    if shown and not shown[0][1].startswith('head_zone'):
        shown = []  # Never happens today: the zone always leads.
    zone = shown[0][0] if shown else ''
    shown = [entry for entry in shown
             if not (entry[1] == 'head_district' and entry[0].casefold() == zone.casefold())]

    def optional(entry: tuple[str, str]) -> bool:
        return entry[1] == 'head_district' or entry[1] == 'head_info' and entry[0] != 'PvP G5+'

    def width_of(entries: list[tuple[str, str]]) -> int:
        return display_width(' '.join(text for text, _ in entries))

    gap = 2
    if notice:
        # The notice replaces the clock, so the header runs to one frame cell
        # before the corner (#261: 78 inside at 80x24, `─┐` closing it).
        area = Rect(area.x, area.y, layout.width - 2 * area.x, area.height)
        fixed = [entry for entry in shown[1:] if not optional(entry)]
        least = zone_cut(zone, display_width(zone_minimum(zone))) if zone else ''
        used = width_of([*([(least, '')] if least else []), *fixed])
        text = clean_text(notice[0])
        if display_width(text) > area.width - used - gap:
            gap = 1
        if display_width(text) > area.width - used - gap:
            text = word_prefix(text, max(0, area.width - used - gap)) if area.width - used - gap > 3 else ''
        right = [(text, notice[1])] if text else []
    right_text = ' '.join(clean_text(text) for text, _ in right)
    room = area.width - (display_width(right_text) + gap if right_text else 0)
    while width_of(shown) > room:
        drop = [i for i, entry in enumerate(shown) if optional(entry)]
        if not drop:
            break
        shown.pop(drop[-1])
    if shown and width_of(shown) > room:
        fixed = width_of(shown[1:]) + 1 if len(shown) > 1 else 0
        shown[0] = (zone_cut(zone, max(0, room - fixed)), shown[0][1])
    shown = [entry for entry in shown if entry[1] == 'head_district'] + [entry for entry in shown if entry[1] != 'head_district']
    x = area.x
    for text, role in shown:
        buffer.put(x, area.y, text, paint(role, bold=role.startswith(('head_zone', 'word_'))))
        x += display_width(text) + 1
    x = area.right - display_width(right_text)
    for text, role in right:
        buffer.put(x, area.y, text, paint(role))
        x += display_width(text) + 1


def rule(buffer: CellBuffer, layout: Layout, paint: Paint, y: int,
         label: str = '', role: str = 'panel_label') -> None:
    area = layout.side
    buffer.put(area.x, y, '─' * area.width, paint('panel_rule'))
    if label:
        buffer.put(area.x + 1, y, f' {label} ', paint(role, bold=role != 'panel_label'))


def bar(buffer: CellBuffer, paint: Paint, x: int, y: int, label: str, value: float,
        maximum: float, role: str, digits: str, width: int = 10) -> None:
    """UI 2.4: ten full/half/empty cells and a number."""
    buffer.put(x, y, label.ljust(4), paint('panel_label'))
    filled = max(0.0, min(1.0, value / max(1, maximum))) * width
    for i in range(width):
        glyph = '█' if i < int(filled) else '▌' if i < filled else '░'
        buffer.put(x + 4 + i, y, glyph, paint(role if i < filled else 'bar_empty'))
    buffer.put(x + 5 + width, y, digits, paint('panel_text'))


@dataclass
class LogEntry:
    kind: str
    text: str
    at: float | None
    arrived: float
    speaker: str = ''
    speaker_role: str = 'log_speaker'
    count: int = 1
    xp: int | None = None
    script: str = ''
    """The authored script line that emitted it; such an entry never merges."""
    lines: list[int] = field(default_factory=list)
    """id() of each raw line it stands for, so the door can tell which were drawn whole."""

    @property
    def shown(self) -> str:
        return self.text + (f' (x{self.count})' if self.count > 1 else '')


SENTENCE_END = re.compile(r'''[.!?…][)\]"'”’]*$''')
"""A part ending in . ! ? or an ellipsis, then any closing quotes or brackets."""


def join_merged(left: str, right: str) -> str:
    """Terminal UI 2.5 (#251): a space after a sentence, `; ` after a fragment."""
    return left + (' ' if SENTENCE_END.search(left.rstrip()) else '; ') + right


def seconds(line: dict[str, Any]) -> float | None:
    try:
        return datetime.fromisoformat(str(line.get('at', ''))).timestamp()
    except ValueError:
        return None


def digest(lines: list[dict[str, Any]]) -> list[LogEntry]:
    """Declared kinds only; XP formatting never determines a line's kind."""
    out: list[LogEntry] = []
    for line in lines:
        kind = line.get('kind')
        if kind == 'notice':
            continue  # P4 owns notices on the existing toast surface.
        if kind not in GUTTERS:
            kind = 'system'
        text, at = clean_text(str(line.get('text', ''))), seconds(line)
        who, role = str(line.get('speaker', '')), str(line.get('speaker_role', 'log_speaker'))
        arrived = float(line.get('_arrived', -1e30))
        same = next((entry for entry in reversed(out)
                     if at is not None and entry.at is not None and 0 <= at - entry.at <= COALESCE_SECONDS
                     and (entry.kind, entry.text, entry.speaker, entry.speaker_role) == (kind, text, who, role)), None)
        if same:
            same.count += 1
            same.lines.append(id(line))
            same.at, same.arrived = at, arrived
            continue
        match = re.fullmatch(r'You gain (\d+) XP\.?', text) if kind == 'reward' else None
        xp = int(match[1]) if match else None
        prev = out[-1] if out else None
        script = str(line.get('script') or '')
        if prev and kind == prev.kind == 'reward' and not script and not prev.script and at is not None and prev.at is not None and 0 <= at - prev.at <= REWARD_SECONDS:
            if xp is not None and prev.xp is not None:
                prev.xp = prev.xp * prev.count + xp
                prev.text = f'{prev.xp} XP'
            else:
                prev.text = join_merged(prev.shown, text)
            prev.count = 1
            prev.at, prev.arrived = at, arrived
            prev.lines.append(id(line))
            continue
        out.append(LogEntry(str(kind), text, at, arrived, who, role, xp=xp, script=script, lines=[id(line)]))
    return out


def _text(entry: LogEntry) -> str:
    text = entry.shown
    if entry.kind == 'speech' and entry.speaker and not text.startswith(entry.speaker + ': '):
        text = entry.speaker + ': ' + text
    return text


def visible_rows(lines: list[dict[str, Any]], width: int, height: int,
                 now: float, release: float | None = None,
                 whole: set[int] | None = None) -> list[tuple[LogEntry, str, bool]]:
    """P12 rule 5 (#202): whole entries; the newest always shows.

    Bounded work (technical review 6808753030): at most the newest
    log_window(height) + LOG_PROTECT_EXTRA lines are digested, and only
    entries the selection reaches are wrapped (fit.select_lazy).
    """
    if height <= 0:
        return []
    lines = list(lines)
    start = max(0, len(lines) - log_window(height))
    # Rule 5 step 2: reach back for protected lines the window cut off,
    # but only while they could still show. At most `height` protected
    # entries fit, so stop once that many distinct ones are in; never go
    # past PROTECT_SECONDS (lines arrive in order) or LOG_PROTECT_EXTRA.
    def protected_line(line: dict[str, Any]) -> bool:
        return (line.get('kind') in ('danger', 'combat') and not released(seconds(line), release)
                and 0 <= now - float(line.get('_arrived', -1e30)) < PROTECT_SECONDS)
    found = {(line.get('kind'), line.get('text')) for line in lines[start:] if protected_line(line)}
    floor = max(0, start - LOG_PROTECT_EXTRA)
    while (start > floor and len(found) < height
           and 0 <= now - float(lines[start - 1].get('_arrived', -1e30)) < PROTECT_SECONDS):
        start -= 1
        if protected_line(lines[start]):
            found.add((lines[start].get('kind'), lines[start].get('text')))
    entries = digest(lines[start:])
    wrapped: dict[int, list[str]] = {}

    def rows_of(index: int) -> list[str]:
        if index not in wrapped:
            wrapped[index] = word_wrap(_text(entries[index]), width - 2, continuation=0)
        return wrapped[index]

    def protected(index: int) -> bool:
        entry = entries[index]
        return (entry.kind in ('danger', 'combat') and not released(entry.at, release)
                and 0 <= now - entry.arrived < PROTECT_SECONDS)

    shown = select_lazy(len(entries), rows_of, protected, height)
    if whole is not None:  # S48c3b: raw lines drawn whole, for the Ctrl-P read-back.
        whole.update(line for index, rows in shown.items() if rows >= len(wrapped[index])
                     for line in entries[index].lines)
    out = []
    for index in sorted(shown):
        rows = wrapped[index]
        if index == len(entries) - 1:
            rows = log_cut(rows, shown[index], width - 2)
        out.extend((entries[index], line, i == 0) for i, line in enumerate(rows[:shown[index]]))
    return out


def released(at: float | None, release: float | None) -> bool:
    """Runner contract amendment 3: the server's `me.danger_release` ends the
    protection of danger and combat lines stamped up to it (their source, the
    training drone, can no longer act). Never inferred from text or timing."""
    return release is not None and at is not None and at <= release


def log(buffer: CellBuffer, layout: Layout, paint: Paint, lines: list[dict[str, Any]], now: float,
        release: float | None = None) -> None:
    area = layout.log
    for index, (entry, text, first) in enumerate(visible_rows(lines, area.width, area.height, now, release)):
        y, role = area.y + index, 'log_' + entry.kind
        if first:
            buffer.put(area.x, y, GUTTERS[entry.kind], paint(role, bold=entry.kind == 'danger'))
        if first and entry.kind == 'speech' and entry.speaker:
            speaker = clean_text(entry.speaker)
            if text.startswith(speaker + ': '):
                text = text[len(speaker) + 2:]
            name = truncate(speaker, max(1, area.width - 6))
            buffer.put(area.x + 2, y, name + ': ', paint(entry.speaker_role if paint.has(entry.speaker_role) else 'log_speaker'))
            x = area.x + 4 + display_width(name)
            buffer.put(x, y, truncate(text, area.right - x), paint('log_speech'))
        else:
            buffer.put(area.x + 2, y, text, paint(role, bold=entry.kind == 'danger'))


def hints(buffer: CellBuffer, layout: Layout, paint: Paint, hint: str, role: str = 'key_text') -> None:
    """Host the exact P3 fitter output, including prompts and channels."""
    buffer.put(layout.hint.x, layout.hint.y, truncate(hint, layout.hint.width), paint(role))
