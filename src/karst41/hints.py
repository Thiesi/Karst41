"""Presentation groups for already selected E/J targets (terminal UI 11)."""

from collections.abc import Mapping
import re
from typing import Any

_KEY = re.compile(r"\{([a-z_0-9]+)\}")
_NAMES = {"tab": "Tab", "enter": "Enter", "escape": "Esc", "up": "Up", "down": "Down",
          "left": "Left", "right": "Right", "pgup": "PgUp", "pgdn": "PgDn", "home": "Home", "end": "End"}
_MOVES = ("move_up", "move_left", "move_down", "move_right")


def key_name(binding: str) -> str:
    """A keymap binding as the hint row writes it: `tab` -> Tab, `f` -> F, `ctrl-p` -> Ctrl-P."""
    parts = binding.split("-")
    if len(parts) > 1 and parts[-1]:
        return "-".join([*(p.title() for p in parts[:-1]), key_name(parts[-1])])
    return _NAMES.get(binding, binding.upper() if len(binding) == 1 else binding.title())


def resolve_keys(text: str, keymap: Any | None) -> str:
    """Fill `{action}` with the caller's key (#220 section 4: key names come
    from the caller's keymap). `{move}` is "Arrow keys" on the arrows map,
    else its four move keys. Raises KeyError for an unknown action."""
    bindings = getattr(keymap, "bindings", None) or {}

    def one(match: re.Match[str]) -> str:
        action = match.group(1)
        if action == "move":
            keys = [bindings.get(m, ("",))[0] for m in _MOVES]
            if keys == ["up", "left", "down", "right"] or not bindings:
                return "Arrow keys"
            return "".join(keys)
        if bindings and action not in bindings:
            raise KeyError(action)
        keys = bindings.get(action)
        return key_name(keys[0]) if keys else action
    return _KEY.sub(one, text)


def focus_context(focus: Mapping[str, str] | None, jack: Mapping[str, str] | None,
                  jack_key: str = 'J') -> list[tuple[str, str]]:
    """Group aliases only when both keys have the same target and verb."""
    if focus and jack and focus['id'] == jack['id'] and focus['verb'] == jack['verb']:
        return [(f"E/{jack_key} {focus['verb']}", focus['label'])]
    return [(f"{key} {target['verb']}", target['label'])
            for key, target in (('E', focus), (jack_key, jack)) if target]
