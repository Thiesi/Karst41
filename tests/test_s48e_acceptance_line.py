"""S48e wording (b): the acceptance line says what it means.

Presentation lead, via the technical lead: "Contract accepted. Nobody can
join it now." (42 cells; the request said 41), toasted and kept whole in the
log (P12).
"""

from pathlib import Path

from karst41.content.loader import load_content
from karst41.door.cells import display_width
from karst41.server.protocol import Frame
from tests.character_helpers import act
from tests.test_s48e_talker_reach import HALO, VESPER, at, talk, toasts

LINE = 'Contract accepted. Nobody can join it now.'


def test_accepting_a_job_toasts_the_line_and_keeps_it_whole_in_the_log():
    assert display_width(LINE) == 42
    content = load_content(Path(__file__).parents[1]/'src/karst41/content')
    game, client, _ = at(content, HALO, VESPER, (9, 14), (8, 13))
    game.core.contracts.game.players[client.session.player_id].progress['wake_combat'] = 'skipped'
    talk(game, client)
    game.frames_for(client)
    for key in 'cay':
        act(game, client, Frame('key', {'k': key, 't': 0}))
    game.commit_commerce()
    assert LINE in toasts(game, client)
    assert LINE in [line['text'] for line in client.session.log_ring]
