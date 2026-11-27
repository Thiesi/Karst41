"""#290: the S10 duel check says which check it missed.

The ledger keeps S10's flags exactly (one sample with both players hurt; one
with shock at 80 or more) and, when one is missing, names it with the duel's
summary and last samples. The hit border keeps its own check and message.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from acceptance_duel import KEPT, Borders, DuelLedger, border_failure  # noqa: E402


def test_a_border_counts_once_per_appearance_not_per_replay():
    borders, on, off = Borders(), list('-----x'), list('KARST ')
    for row in (off, on, on, on, off, on, off, off):
        borders.see(row)
    assert borders.count == 2


def f(health: int, shock: float = 60, stance: str = 'normal', hits: int = 0, health_max: int = 100) -> dict:
    return {'health': health, 'health_max': health_max, 'shock': shock, 'stance': stance, 'hit_serial': hits}


def test_an_even_duel_misses_nothing_and_its_summary_counts_hits_and_borders():
    duel = DuelLedger(('A', 'B'), borders_before=3)
    duel.sample(0.2, (f(100, hits=4), f(100, hits=7)), borders=3)
    duel.sample(0.4, (f(90, 70, hits=5), f(100, hits=7)), borders=4)
    duel.sample(0.6, (f(90, 85, hits=5), f(80, hits=8)), borders=4)
    duel.sample(0.8, (f(60, hits=6), f(0, stance='down', hits=10)), borders=5)
    assert duel.missed() == []
    summary = duel.summary(0.8, 'B')
    assert summary['hits_taken'] == {'A': 2, 'B': 3}
    assert summary['max_shock'] == {'A': 85, 'B': 60}
    assert summary['first_hurt_s'] == {'A': 0.4, 'B': 0.6}
    assert summary['a_borders'] == {'before': 3, 'during': 2}
    json.dumps(summary)  # The DUEL line is JSON.


def test_a_one_sided_duel_names_both_hurt_and_shows_the_last_samples():
    duel = DuelLedger(('A', 'B'), borders_before=1)
    for step in range(20):
        duel.sample(0.2*(step+1), (f(100, 85), f(100-5*step, stance='down' if step == 19 else 'normal', hits=step)), borders=1)
    assert duel.missed() == ['both_hurt']
    message = duel.failure('132x50', 4.0, 'B')
    assert message.startswith('S10 duel 132x50: missed both_hurt')
    assert 'one-sided' in message and 'shock_seen:' not in message
    assert '"hits_taken": {"A": 0, "B": 19}' in message and '"during": 0' in message
    samples = [line for line in message.splitlines() if line.startswith('    {')]
    assert len(samples) == KEPT and json.loads(samples[-1])['B']['stance'] == 'down'


def test_shock_just_under_the_threshold_is_named():
    duel = DuelLedger(('A', 'B'), borders_before=0)
    duel.sample(0.2, (f(90, 79.9), f(80, 79)), borders=1)
    assert duel.missed() == ['shock_seen']
    assert 'shock at 80 or more' in duel.failure('80x24', 0.2, 'A')


def test_both_flags_need_one_sample_each_as_before():
    """Hurt at different samples is not both hurt in one sample: S10's meaning, unchanged."""
    duel = DuelLedger(('A', 'B'), borders_before=0)
    duel.sample(0.2, (f(90), f(100)), borders=0)
    duel.sample(0.4, (f(100, health_max=100), f(90)), borders=0)
    assert duel.missed() == ['both_hurt', 'shock_seen']


def test_the_border_failure_gives_every_phase():
    message = border_failure('80x24', 0, 0, 0, {'loser': 'B'})
    assert 'missed hit_border_seen' in message
    assert 'before the duel 0, during 0, after 0' in message and '"loser": "B"' in message
