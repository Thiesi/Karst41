"""#277 (C3): record_use's draft deep-copies only the fields gain() writes.

`skills.proposal(player)` replaces `deepcopy(player)` as gain()'s draft.
For every action and line, across days, repeated receipts, caps and the
stealth encounter budget, gain() must give the same amount and the same
progress, skills and attributes on either draft, and leave the live
player untouched. A guard fails, naming the field, if gain() ever writes
a Player field outside skills.GAIN_FIELDS.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from karst41.content.loader import load_content
from karst41.server.skills import GAIN_FIELDS, USE_RULES, Use, gain, proposal
from tests.character_helpers import create
from tests.harness import HeadlessServer
from tests.server.test_277_record import same

DAY = datetime(2026, 11, 26, 23, 59, 30, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def player() -> Any:
    game = HeadlessServer(load_content(Path(__file__).parents[2] / "src/karst41/content"), character_state={})
    client = game.connect()
    create(game, client, archetype=0, name="Draft", card="none")
    game.commit_characters()
    return game.core.characters.players[client.session.player_id]


def uses() -> list[Use]:
    found = []
    for action, (lines, _) in USE_RULES.items():
        for line in lines:
            common = dict(eligible=True, changed=True, threat=True, authored=True)
            found.append(Use(action, line, f"r:{action}:{line}", **common))
            found.append(Use(action, line, f"r:{action}:{line}", **common, seconds=40, encounter="e1"))
    found.append(Use("weapon_hit", "kinetic", "r:ineligible"))  # gain() returns 0 early.
    found.append(Use("weapon_hit", "melee", "r:wrong-line", eligible=True, threat=True))
    return found


def states(player: Any) -> list[tuple[str, Any]]:
    """Ledger states gain() branches on: empty, same day, used receipt, near caps, a stealth budget."""
    found = [("fresh", player)]
    day = DAY.date().isoformat()
    for name, ledger, skills in [
        ("same day", {"day": day, "gains": {"kinetic": .4}, "receipts": ["older"], "stealth": {}}, {}),
        ("receipt used", {"day": day, "gains": {}, "receipts": [f"r:{a}:{l}" for a, (ls, _) in USE_RULES.items() for l in ls]}, {}),
        ("daily cap", {"day": day, "gains": {l: 2.0 for _, (ls, _) in USE_RULES.items() for l in ls}, "receipts": []}, {}),
        ("skill max", {}, {l: 100 for _, (ls, _) in USE_RULES.items() for l in ls}),
        ("yesterday, stealth budget", {"day": "2026-11-25", "gains": {"stealth": 1}, "receipts": ["x"],
                                       "stealth": {"id": "e1", "gain": .7}}, {}),
    ]:
        variant = deepcopy(player)
        variant.progress = {**variant.progress, "skill_use": ledger, "other": {"nested": [1, {"deep": True}]}}
        variant.skills = {**variant.skills, **skills}
        found.append((name, variant))
    return found


def changed_fields(before: Any, after: Any) -> list[str]:
    return [f.name for f in fields(before) if getattr(before, f.name) != getattr(after, f.name)]


def test_gain_on_the_slim_draft_matches_gain_on_a_full_copy(player) -> None:
    checked = gained = 0
    for state, live in states(player):
        for use in uses():
            for now in (DAY, DAY + timedelta(minutes=1)):  # Same day, and across midnight.
                untouched = deepcopy(live)
                full, slim = deepcopy(live), proposal(live)
                want = gain(full, use, now)
                got = gain(slim, use, now)
                assert got == want, f"{state}, {use.action}/{use.line}: amount {got} != {want}"
                for name in GAIN_FIELDS:
                    same(getattr(full, name), getattr(slim, name), f"{state}, {use.action}/{use.line}: {name}")
                assert changed_fields(untouched, live) == [], f"{state}, {use.action}: gain() changed the live player"
                checked += 1
                gained += bool(want)
    assert gained > 20 and checked - gained > 20  # Both the gaining and the early-return branches ran.


def test_guard_gain_writes_no_player_field_outside_gain_fields(player) -> None:
    """If this fails, gain() writes a field the slim draft shares with the live
    player: add it to skills.GAIN_FIELDS (or go back to deepcopy(player))."""
    for state, live in states(player):
        for use in uses():
            draft = deepcopy(live)
            gain(draft, use, DAY)
            extra = [name for name in changed_fields(live, draft) if name not in GAIN_FIELDS]
            assert not extra, (f"gain() wrote Player.{', Player.'.join(extra)} ({state}, {use.action}/{use.line}); "
                               f"the slim draft in record_use deep-copies only {', '.join(GAIN_FIELDS)}")


def test_the_proposal_shares_nothing_gain_writes_with_the_live_player(player) -> None:
    draft = proposal(player)
    for name in GAIN_FIELDS:
        assert getattr(draft, name) is not getattr(player, name)
        assert getattr(draft, name) == getattr(player, name)
    draft.progress.setdefault("skill_use", {}).setdefault("receipts", []).append("probe")
    assert "probe" not in player.progress.get("skill_use", {}).get("receipts", [])
