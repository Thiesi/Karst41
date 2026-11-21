"""S48e: a truce zone's price notice ends with the zone (presentation lead,
7054104805).

The native evening showed "Fire here and Sable comes. No Halo." on the hint
row in the Warden Precinct (SAFE) after the Row death, in all six cells.
The door holds a price notice until it has been up 8 s and a key has been
pressed (terminal UI 2.1), and feedback toasts push it back into the queue.
So a caller who fires, dies and decants without a key in between had the
Row's rule follow them into a zone where it doesn't apply. Leaving the truce
zone, the decant included, now drops that zone's price notices, shown or
queued.
"""

from karst41 import truce_text
from tests.test_s48e_evening import ROW, Caller, Walk


def test_a_decant_after_a_row_death_clears_the_truce_hint():
    walk = Walk()
    caller = Caller(walk.game, 1, 'Vex', 80, 24, walk.seen)
    walk.callers.append(caller)
    walk.tick(5)
    walk.first_screen(caller)
    walk.drone(caller, skip=True)
    walk.door(caller)
    walk.record(caller, 3)
    walk.route(caller, 'vatside-clinics', 'core-plaza', ROW)
    walk.breach(caller)  # Fires on a regular, Sable answers, the decant.
    assert caller.actor.zone == 'core-precinct'
    keeper = walk.game.core.world.content.factions['sable'].short
    texts = {truce_text.arrival(keeper), truce_text.at_npc(keeper), truce_text.at_player()}
    present = caller.models['present']
    assert present.price_noticed, 'the Row never raised its first-target price notice'
    screens = caller.screens()
    for kind, model in caller.models.items():
        shown = model.toast[0] if model.toast else ''
        assert shown not in texts and not texts & {entry[0] for entry in model.notice_queue}, (kind, shown)
        hint = screens[kind].splitlines()[-2]
        assert 'Sable comes' not in hint and 'No Halo' not in hint, (kind, hint)
