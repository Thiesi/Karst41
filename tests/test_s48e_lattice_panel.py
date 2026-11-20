"""S48e walk finding 4: the Lattice side panel cuts a row at a whole word.

Presentation lead: at 80x24 the panel cut Vesper's release stage inside a
word ("5s E: manual 5 s, n..."). It now uses terminal UI 6's whole-word cut
(#261) for any row too wide for the panel; how the panel shows an objective
(wrap or an authored short form) is carried to the re-rank.
"""

import re

from tests.character_helpers import place
from tests.test_s48e_evening import Caller, Walk


def test_the_release_stage_is_cut_at_a_whole_word_in_the_80x24_panel():
    walk = Walk()
    caller = Caller(walk.game, 1, 'Ilse', 80, 24, walk.seen)
    walk.callers.append(caller)
    walk.tick(5)
    walk.drone(caller, skip=True)
    walk.door(caller)
    walk.record(caller, 2)
    owner, pid = walk.game.core.contracts, caller.session.player_id
    # Fixture: the job taken and its first stage recorded through the engine;
    # the panel is what this test is about.
    proposal = owner.propose(pid, owner.offer('vesper-first-thing', pid, 'vesper'), (pid,))
    owner.consent(proposal.id, pid)
    owner.start(proposal.id, pid)
    walk.game.commit_commerce()
    iid = next(i for i, j in owner.state.engine.instances.items() if j.template == 'vesper-first-thing')
    place(walk.game, caller.client, 'terraces-blocks', (73, 25))
    walk.tick(5)
    owner.actions.perform(caller.session, iid, owner.state.engine.instances[iid].stages[0].targets[0])
    walk.tick(5)
    walk.walk(caller, (71, 25), adjacent=True)
    walk.tick(3)
    caller.press('j')
    walk.tick(10)
    assert pid in walk.game.core.lattice.presences
    # Both renderers draw this panel with the same render_lattice; the
    # default one is the caller's own screen.
    for kind, screen in (('present', caller.screen()),):
        panel = [row.split('│')[-1].strip('║ ') for row in screen.splitlines() if '│' in row]
        cut = [row for row in panel if row.endswith('...')]
        assert cut, (kind, panel)
        for row in cut:
            # Whole words only: what precedes the cut ends a word of the full text.
            assert re.search(r'(^|\s)\S+\.\.\.$', row), (kind, row)
        assert not any(row.endswith(', n...') for row in panel), (kind, panel)
        assert '5s E: manual 5 s,...' in panel, (kind, panel)
