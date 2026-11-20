"""S48e: Okafor answers what the clinic charges; no dead counter offer ships.

Technical lead's ruling on the walk's Okafor finding: no new rule. Her
"Clinic terms" service named a counter the Wake Hall does not have, so it
always refused. The creative lead's words replace it with an ask (an
existing mechanic that grants nothing), and the talker-reach check now
reports any offer bound to no counter in its talker's zone.
"""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from karst41.content.loader import load_content
from karst41.content.schema import Offer
from karst41.content.validate import validate
from tests.character_helpers import create, place
from tests.harness import HeadlessServer
from tests.test_dialogue_runtime import press

HALL = 'vatside-wake-hall'
ANSWER = ("Clinic Row, out the door. Fitting's a fifth of the part's price, on top of the part if they "
          "sell it. Two in a hundred don't take: the part's gone and you shake a while. The counter "
          "shows the real sum.")


@pytest.fixture(scope='module')
def content():
    return load_content(Path(__file__).parents[1]/'src/karst41/content')


def beside_okafor(content):
    game = HeadlessServer(content, character_state={})
    caller = game.connect({'user_id': 1, 'handle': 'Patient', 'node_name': 'S48e'})
    game.step(2)
    create(game, caller, card='none')
    zone = game.core.world.zones[HALL]
    okafor = next(a for a in zone.actors.values() if a.behaviour.get('template') == 'npc-cast-okafor')
    okafor.behaviour.update(home=okafor.position, pause=1 << 40)
    x, y = okafor.position
    tile = next(t for t in ((x-1, y), (x, y-1), (x+1, y), (x, y+1))
                if zone.passable(t) and not any(a.position == t for a in zone.actors.values()))
    place(game, caller, HALL, tile)
    game.step(2)
    return game, caller


def test_okafor_answers_what_the_clinic_charges_and_grants_nothing(content):
    game, caller = beside_okafor(content)
    sid = caller.session.id
    press(game, caller, 'e')
    game.commit_commerce()
    game.step(2)
    assert game.core.dialogue.menus[sid].contact.talker == 'okafor'
    body = game.core.characters.sim.external_views[sid][1]
    assert body['actions'] == [{'key': 'a', 'label': 'What does the clinic charge?'}]
    # A caller in the Wake hears her situation line; the greeting is for later visits.
    greeting = next(line for line in content.dialogue['okafor'].lines if line.id == 'greeting')
    assert greeting.text == ("Up you get. Ask me about the clinic before you sit down. "
                             "Nobody needs a speech from me as well.")
    press(game, caller, 'a')
    game.commit_commerce()
    game.step(2)
    log = [line['text'] for line in caller.session.log_ring]
    assert any(ANSWER in text for text in log), log[-5:]
    assert sid not in game.core.commerce.menus and sid not in game.core.commerce.reach
    assert sid not in game.core.progression.clinics.menus


def test_shipped_content_has_no_counter_offer_without_its_counter(content):
    assert not validate(content)


def test_an_offer_bound_to_no_counter_in_the_zone_is_reported(content):
    dead = deepcopy(content)
    okafor = dead.dialogue['okafor']
    dead.dialogue['okafor'] = replace(okafor, offers=(
        Offer(kind='service', label='Clinic terms', hotkey='s', ref='sablier-clinic'),))
    problems = [str(p) for p in validate(dead) if 'no counter in this zone is bound to it' in str(p)]
    assert len(problems) == 1 and 'cast-okafor' in problems[0] and 'Clinic terms' in problems[0], problems
