"""The shared frame, view headers, shells and transient chrome.

P2 preserves the existing draw order and glyph/style output. Content
renderers receive Layout and draw only inside their content rectangles.
"""

import re
from typing import Any

from karst41.layout import Layout, Rect
from karst41.door.cells import CellBuffer, clean_text, display_width, truncate
from karst41.door.palette import Palette
from karst41.door.fit import default_log_rows


def box(buffer: CellBuffer, rect: Rect, palette: Palette, title: str = "") -> None:
    x, y, w, h = rect.x, rect.y, rect.width, rect.height
    style = palette.style("border")
    buffer.fill(x, y, w, h, palette.style())
    buffer.put(x, y, "┌" + "─" * (w - 2) + "┐", style)
    buffer.put(x, y + h - 1, "└" + "─" * (w - 2) + "┘", style)
    for row in range(y + 1, y + h - 1):
        buffer.put(x, row, "│", style)
        buffer.put(x + w - 1, row, "│", style)
    if title:
        buffer.put(x + 2, y, truncate(" " + title + " ", w - 4), palette.style("title"))


def _objective_hint(objective: dict[str, Any], width: int | None = None) -> str:
    """UI 11: retain Next, its verb, progress and the server's current cost."""
    action = clean_text(str(objective['next_action'])).strip()
    verb, _, target = action.partition(' ')
    prefix = 'Next: ' + verb
    suffix = f" {objective['progress']} | {objective['cost']}"
    suffix = clean_text(suffix)
    if target and width is not None:
        target = truncate(target, max(0, width-display_width(prefix+' '+suffix)))
    return prefix + (' '+target if target else '') + suffix


TRAILING_PAREN = re.compile(r'\s*\([^()]*\)\s*$')


def short_name(label: str) -> str:
    """Terminal UI 11 (#253): a shortened E/J name keeps its last whole word
    before any trailing parenthetical, skipping words that are only digits or
    punctuation: `Public terminal (Plaza 1)` becomes `terminal`, `Locker 7`
    becomes `Locker`, `Dr. Ilse Vantongeren` becomes `Vantongeren`. With no
    such word left, the label stays whole and the normal drop order applies."""
    words = [word for word in TRAILING_PAREN.sub('', clean_text(label)).split() if any(ch.isalpha() for ch in word)]
    return words[-1] if words else clean_text(label).strip()


# Terminal UI 6 (#261): honorifics and initials never stand for a person.
HONORIFIC = re.compile(r'^(?:Dr|Mr|Mrs|Ms|Mx)\.$|^\w\.$')


def word_prefix(text: str, budget: int) -> str:
    """The longest whole-word prefix of `text` that fits `budget` with `...`.

    Only a first word that alone can't fit is cut inside, to fit with `...`
    (terminal UI 6, #261): the one in-word cut, for a word over budget - 1.
    """
    words = clean_text(text).split()
    shown = ''
    for word in words:
        candidate = (shown + ' ' + word).strip()
        if display_width(candidate + '...') > budget:
            break
        shown = candidate
    if shown:
        return shown + '...'
    return truncate(words[0] if words else '', budget)


ARTICLES = ('the', 'a', 'an')


def zone_minimum(name: str) -> str:
    """A cut zone name's least: its first whole word after a leading article, with `...`."""
    words = clean_text(name).split()
    if len(words) > 1 and words[0].casefold() in ARTICLES:
        words = words[1:]
    return (words[0] + '...') if words else ''


def zone_cut(name: str, budget: int) -> str:
    """Terminal UI 2 (#261): the zone whole, else a whole-word prefix with
    `...` that leaves out a leading article (`The Tin Halo` cut is `Tin...`);
    only when its first word can't fit is that word cut inside itself."""
    name = clean_text(name).strip()
    if display_width(name) <= budget:
        return name
    words = name.split()
    if len(words) > 1 and words[0].casefold() in ARTICLES:
        words = words[1:]
    return word_prefix(' '.join(words), budget)


def here_label(label: str, budget: int) -> str:
    """Terminal UI 6, HERE (#261), first that fits: the label; its #253 short
    form keeping the trailing parenthetical; the short form alone; a
    whole-word prefix with `...`."""
    label = clean_text(label).strip()
    if display_width(label) <= budget:
        return label
    short = short_name(label)
    paren = TRAILING_PAREN.search(label)
    for candidate in ((short + ' ' + paren.group().strip()) if paren else '', short):
        if candidate and candidate != label and display_width(candidate) <= budget:
            return candidate
    return word_prefix(label, budget)


def person_word(name: str) -> str:
    """A name's last whole word, skipping honorifics, initials and words of
    only digits or punctuation; empty when nothing is left to choose."""
    words = [word for word in clean_text(name).split()
             if any(ch.isalpha() for ch in word) and not HONORIFIC.match(word)]
    return words[-1] if words else ''


def panel_names(names: list[str], budgets: list[int]) -> list[str]:
    """Terminal UI 6 (#261): the side panel's name rows, NEARBY and TARGET
    together, from presented names only. Pass only the rows that are drawn.

    Each row shows its full name when it fits, else its last whole word
    (`person_word`). Two rows that would then read the same while their full
    names differ both fall back to a whole-word prefix with `...`; rows
    whose full names are equal are unaffected. A name with no word left to
    choose uses the prefix.
    """
    names = [clean_text(name).strip() for name in names]
    shown = []
    for name, budget in zip(names, budgets):
        word = person_word(name)
        shown.append(name if display_width(name) <= budget
                     else word if word and display_width(word) <= budget else word_prefix(name, budget))
    collide = [any(shown[i] == shown[j] and names[i] != names[j] for j in range(len(names)) if j != i)
               for i in range(len(names))]
    return [word_prefix(name, budget) if clash and text != name else text
            for name, budget, text, clash in zip(names, budgets, shown, collide)]


def lesson_hint(lesson: dict[str, Any], width: int, context: list[tuple[str, str]]) -> str:
    """Terminal UI 11 (presentation lead's build for S48c2).

    The lesson hint (or errand), then the E/J context, then `? help`, two
    spaces apart; no other key group. Too wide: the errand's short form
    first, then the context's short form, then no context, then no `? help`.
    The lesson hint itself is never cut.
    """
    full_ctx = [(prefix + ' ' + clean_text(label)).rstrip() for prefix, label in context]
    # Short form (terminal UI 11, #253): the key and verb, then the name's
    # last whole word before any trailing parenthetical, never an ellipsis:
    # `E talk to Vantongeren`, `E jack in at terminal`, `E use door`.
    # Two short forms that would read the same both stay whole (#253).
    shorts = [short_name(label) for _, label in context]
    short_ctx = [(prefix + ' ' + (clean_text(label).strip() if shorts.count(short) > 1 else short)).rstrip()
                 for (prefix, label), short in zip(context, shorts)]
    text, short = clean_text(str(lesson.get('text', ''))), clean_text(str(lesson.get('short', '')))
    heads = [text] + ([short] if short and short != text else [])
    for head in heads:
        for groups in (full_ctx, short_ctx):
            row = '  '.join([head, *groups, '? help'])
            if display_width(row) <= width:
                return row
    head = heads[-1]
    for row in ('  '.join([head, '? help']), head):
        if display_width(row) <= width:
            return row
    return head


def focus_hint(hint: str, width: int, context: list[tuple[str, str]],
               objective: dict[str, Any] | None = None, *,
               fit_all: bool = False, primary: bool = False,
               objective_in_panel: bool = False, escape: str = '') -> str:
    """UI 11: objective/cost, E/J, then whole help groups; never clip a key."""
    raw = '  '.join((prefix + ' ' + label).rstrip() for prefix, label in context)
    if primary:
        # A prompt/channel is one priority field, not optional help groups.
        if escape:
            escape = truncate(clean_text(escape), width)
            field = truncate(clean_text(hint), max(0, width - display_width(escape) - 2))
            return '  '.join(text for text in (field, escape) if text)
        return truncate(clean_text(hint), width)
    if not fit_all and ((not raw and not objective) or raw and not hint.startswith(raw)):
        return hint
    if raw and not hint.startswith(raw):
        context, raw = [], ''
    labels = [clean_text(label).strip() for _, label in context]
    prefixes = [clean_text(prefix) for prefix, _ in context]
    limits = [display_width(label) for label in labels]
    # A shortened name must retain its whole first word and the ellipsis.
    minima = [min(limit, display_width(label.split()[0])+3) if ' ' in label else limit
              for label, limit in zip(labels, limits)]
    objective_group = _objective_hint(objective) if objective and not objective_in_panel else ''

    def fixed_width() -> int:
        groups = [prefix+(' ' if label else '') for prefix, label in zip(prefixes, labels)]
        return display_width('  '.join(([objective_group] if objective_group else [])+groups))

    # Try the shortest legal names before dropping J, then E. The full
    # objective only shortens after both lower-priority groups are gone.
    while prefixes and fixed_width()+sum(minima) > width:
        if len(prefixes) == 1 and prefixes[0].startswith('E/'):
            # A combined group still drops the J alias before its E action.
            prefixes[0] = 'E '+prefixes[0].partition(' ')[2]
            continue
        prefixes.pop()
        labels.pop()
        limits.pop()
        minima.pop()
    if objective and not prefixes and not objective_in_panel:
        objective_group = _objective_hint(objective, width)
    budgets = minima[:]
    available = max(0, width-fixed_width()-sum(budgets))
    for _ in range(available):
        eligible = [i for i in range(len(labels)) if budgets[i] < limits[i]]
        if not eligible:
            break
        index = min(eligible, key=lambda i: budgets[i])
        budgets[index] += 1
    groups = ([objective_group] if objective_group else []) + [(prefix + (' ' + truncate(label, budget) if label else ''))
              for prefix, label, budget in zip(prefixes, labels, budgets)]
    tail = [group for group in hint[len(raw):].strip().split('  ') if group]
    if objective:
        # The legacy objective string also carries its action/journal help.
        # The structured objective above owns the text and unabridged cost.
        tail = ['Q jobs' if 'Next:' in group else group for group in tail]
    tail = ['? help', *(group for group in tail if group != '? help')]
    for group in tail:
        if display_width('  '.join([*groups, group])) <= width:
            groups.append(group)
    return '  '.join(groups)


def render_frame(buffer: CellBuffer, layout: Layout, palette: Palette, header: str,
                 lines: list[dict[str, Any]], hint: str) -> None:
    box(buffer, layout.frame, palette, truncate(header, layout.header.width))
    style = palette.style("border")
    buffer.put(layout.separator.x, layout.separator.y, "├" + "─" * (layout.separator.width - 2) + "┤", style)
    for y in range(layout.divider.y, layout.divider.bottom):
        buffer.put(layout.divider.x, y, "│", style)
    for index, line in enumerate(default_log_rows(lines, layout.log.width, layout.log.height)):
        buffer.put(layout.log.x, layout.log.y + index, line["text"], palette.style(line["role"]))
    buffer.put(layout.hint.x, layout.hint.y, truncate(hint, layout.hint.width), palette.style("hint"))


def _zone_header(buffer: CellBuffer, layout: Layout, body: dict[str, Any], palette: Palette) -> None:
    me = body["me"]
    slots = layout.header_slots
    # Terminal UI 2 (#261): the zone is never cut inside a word.
    header = zone_cut(str(me['zone_name']), slots.zone_title.width - 2) + "  "
    buffer.put(slots.zone_title.x, slots.zone_title.y, truncate(header, slots.zone_title.width), palette.style("title"))
    safety = "CONTESTED─TRUCE─PvP G5+" if me["safety"] == "contested" and me.get("truce") else me["safety"].upper()
    sx = min((slots.heat_safety_right if me.get("heat_until") else slots.safety_right) - len(safety), slots.zone_title.x + display_width(header))
    buffer.put(sx, slots.zone_title.y, safety, palette.style("safety_" + me["safety"]))
    start = sx+len(safety)+2
    if me.get("hunted"):
        # UI 2.1 (S44): HUNTED is a danger fact; it comes before the service summary.
        buffer.put(start, slots.zone_title.y, truncate("HUNTED", max(0, slots.service_right-start)), palette.style("warning"))
        start += len("HUNTED")+2
    if service := body.get('service'):
        buffer.put(start, slots.zone_title.y, truncate(service.get('summary', ''), max(0, slots.service_right-start)), palette.style('ui_highlight'))
    buffer.put(slots.clock.x, slots.clock.y, me.get("clock", ""), palette.style("title"))
    if me.get("heat_until"):
        buffer.put(slots.heat.x, slots.heat.y, "HEAT", palette.style("warning"))


def render_view_shell(buffer: CellBuffer, layout: Layout, palette: Palette,
                      kind: str, body: dict[str, Any]) -> None:
    if kind == "menu":
        box(buffer, layout.overlay, palette, str(body.get("title", "")))
    elif kind == "zone":
        _zone_header(buffer, layout, body, palette)
    elif kind == "lattice":
        area = layout.header_slots.lattice_title
        buffer.put(area.x, area.y, truncate(str(body.get("title", "LATTICE")), area.width), palette.style("title"))


def render_effects(buffer: CellBuffer, layout: Layout, palette: Palette,
                   toast: tuple[str, str, float] | None, flashed: bool) -> None:
    if toast:
        buffer.put(layout.log.x, layout.separator.y, truncate(toast[0], layout.log.width), palette.style(toast[1]))
    if flashed:
        for x in range(layout.frame.x, layout.frame.right - 1):
            buffer.put(x, layout.frame.y, "-", palette.style("warning"))
        for y in range(layout.frame.y + 1, layout.frame.bottom - 1):
            buffer.put(layout.frame.x, y, "|", palette.style("warning"))
            buffer.put(layout.frame.right - 1, y, "|", palette.style("warning"))


def cursor(layout: Layout, hint: str, visible: bool) -> bytes:
    if not visible:
        return b"\x1b[?25l"
    column = min(layout.hint.right, layout.hint.x + 1 + display_width(hint))
    return f"\x1b[{layout.hint.y + 1};{column}H\x1b[?25h".encode("ascii")
