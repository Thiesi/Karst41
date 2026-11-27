"""#290: the S10 duel's ledger, so a failed duel check says which check it missed.

The duel samples both players every 0.2 s. The flags keep S10's meaning
exactly (technical lead, #290: no check is loosened):
- `both_hurt`: one sample where both players' health is below max;
- `shock_seen`: one sample where either player's shock is 80 or more.

The ledger also keeps what a failure needs: the last samples, the hits
each player took (`hit_serial`), each one's highest shock and when each was
first hurt. Caller A's hit borders are counted per phase (before the duel,
during it, after it), but the border check keeps its S10 meaning: any border
on A's screen.
"""

from __future__ import annotations

from collections import deque
import json
from typing import Any

SHOCK_SEEN = 80
KEPT = 12


class Borders:
    """Caller A's hit borders, counted each time one appears on row 0 (not per replay while it stays)."""

    def __init__(self) -> None:
        self.count = 0
        self.shown = False

    def see(self, row: list[str]) -> None:
        shown = row[:5] == ["-"]*5
        if shown and not self.shown:
            self.count += 1
        self.shown = shown


def fighter(actor: Any) -> dict[str, Any]:
    """The sampled fields of one duelling actor."""
    return {"health": actor.health, "health_max": actor.health_max, "shock": actor.shock,
            "stance": actor.stance, "hit_serial": int(actor.behaviour.get("hit_serial", 0))}


class DuelLedger:
    def __init__(self, names: tuple[str, str], borders_before: int, *, kept: int = KEPT) -> None:
        self.names = names
        self.borders_before = borders_before
        self.borders_during = 0
        self.samples: deque[dict[str, Any]] = deque(maxlen=kept)
        self.count = 0
        self.both_hurt = False
        self.shock_seen = False
        self.first_hit: dict[str, int | None] = {name: None for name in names}
        self.last_hit: dict[str, int | None] = {name: None for name in names}
        self.max_shock: dict[str, float] = {name: 0 for name in names}
        self.first_hurt: dict[str, float | None] = {name: None for name in names}

    def sample(self, seconds: float, fighters: tuple[dict[str, Any], dict[str, Any]], borders: int) -> None:
        """One 0.2 s sample: the time since the duel began, both fighters, A's border count so far."""
        self.count += 1
        self.borders_during = borders-self.borders_before
        # The same expressions as S10 before #290.
        self.shock_seen |= any(f["shock"] >= SHOCK_SEEN for f in fighters)
        self.both_hurt |= all(f["health"] < f["health_max"] for f in fighters)
        for name, f in zip(self.names, fighters):
            if self.first_hit[name] is None:
                self.first_hit[name] = f["hit_serial"]
            self.last_hit[name] = f["hit_serial"]
            self.max_shock[name] = max(self.max_shock[name], f["shock"])
            if self.first_hurt[name] is None and f["health"] < f["health_max"]:
                self.first_hurt[name] = round(seconds, 1)
        self.samples.append({"t": round(seconds, 1), "borders": self.borders_during,
                             **{name: dict(f) for name, f in zip(self.names, fighters)}})

    def hits(self, name: str) -> int:
        """Hits taken across the sampled duel. The first sample already follows the first F press,
        so a hit landing before it is not counted here (but still shows in health)."""
        first, last = self.first_hit[name], self.last_hit[name]
        return 0 if first is None or last is None else last-first

    def summary(self, seconds: float, loser: str) -> dict[str, Any]:
        return {"seconds": seconds, "loser": loser, "samples": self.count,
                "both_hurt": self.both_hurt, "shock_seen": self.shock_seen,
                "hits_taken": {name: self.hits(name) for name in self.names},
                "max_shock": self.max_shock, "first_hurt_s": self.first_hurt,
                "a_borders": {"before": self.borders_before, "during": self.borders_during}}

    def missed(self) -> list[str]:
        """The duel's own flags that are still false (the border is checked where S10 always checked it)."""
        return [name for name, ok in (("both_hurt", self.both_hurt), ("shock_seen", self.shock_seen)) if not ok]

    def failure(self, size: str, seconds: float, loser: str) -> str:
        missed = self.missed()
        lines = [f"S10 duel {size}: missed {', '.join(missed)}",
                 *(f"  {name}: {EXPLAIN[name]}" for name in missed),
                 f"  summary: {json.dumps(self.summary(seconds, loser), sort_keys=True)}",
                 f"  last {len(self.samples)} samples:",
                 *(f"    {json.dumps(s, sort_keys=True)}" for s in self.samples)]
        return "\n".join(lines)


EXPLAIN = {
    "both_hurt": "no sample had both players below max health (a one-sided duel)",
    "shock_seen": f"no sample had either player's shock at {SHOCK_SEEN} or more",
}


def border_failure(size: str, before: int, during: int, after: int, duel: dict[str, Any]) -> str:
    return (f"S10 duel {size}: missed hit_border_seen: caller A's screen never showed the hit border "
            f"(borders before the duel {before}, during {during}, after {after}); "
            f"duel: {json.dumps(duel, sort_keys=True)}")
