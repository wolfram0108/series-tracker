"""A player asked on the videos of a whole page (modules/sources/players/asking.py): an answer that stayed unsure under
the load of the read is looked at again when the rest is done, cause by cause — the rule of every source."""
from __future__ import annotations

from modules.sources import policy
from modules.sources.players.asking import ask_many, outcome_of
from modules.sources.players.contract import Answer, Kind, PlayerRef, Reading
from modules.sources.policy import Pacer


class Hosted:
    """A player whose host answers each video by a script of statuses (the last one repeats): 200 is the video."""
    name, label, series_scope = "hosted", "Hosted", False

    def __init__(self, scripts: dict[str, list[int | None]]) -> None:
        self.scripts = scripts
        self.asked: dict[str, int] = {}

    def find(self, text, page_url):
        return None

    async def fetch(self, ref, http, page_url):
        script = self.scripts[ref.url]
        self.asked[ref.url] = self.asked.get(ref.url, 0) + 1
        return Answer(script[min(self.asked[ref.url] - 1, len(script) - 1)], "video")

    def read(self, answer):
        if answer.status is None:
            return Reading(Kind.NO_ANSWER, message="ConnectError: dead.host")
        if answer.status != 200:
            return Reading(Kind.HTTP_ERROR, answer.status)
        return Reading(Kind.PLAYABLE, 200, (720,))


def refs_of(player: Hosted) -> list[PlayerRef]:
    return [PlayerRef(player.name, url) for url in player.scripts]


async def test_a_refusal_under_the_load_of_the_read_is_not_the_answer_the_video_is_asked_again_when_the_rest_is_done():
    """A host asked for every video of a page at once answers 500 to some of them for as long as the read goes on. One
    of them asked again when the rest is done plays: it was the load, and every video of that cause is asked again."""
    attempts = policy.DEFAULT.attempts
    busy = [500] * attempts + [200]
    player = Hosted({"https://h/1": [200], "https://h/2": list(busy), "https://h/3": list(busy), "https://h/4": [200]})
    told: list[tuple[int, int]] = []
    verdicts = await ask_many(player, refs_of(player), None, "https://page", Pacer(0.0),
                              on_recheck=lambda current, total: told.append((current, total)))
    states = [outcome_of(verdicts[ref], ref, player.label).state.value for ref in refs_of(player)]
    assert states == ["playable"] * 4
    assert player.asked == {"https://h/1": 1, "https://h/2": attempts + 1, "https://h/3": attempts + 1, "https://h/4": 1}
    # The read tells how far the second look is: a phase of its own, counted by videos.
    assert told == [(0, 2), (1, 2), (2, 2)]


async def test_a_way_that_is_down_is_asked_again_for_one_video_and_the_others_keep_the_answer():
    """Forty videos lie on a host that takes no connection. One of them asked again says the same: the way is down, and
    the read does not ask the other thirty-nine again. A video whose host answers is not touched."""
    attempts = policy.DEFAULT.attempts
    player = Hosted({**{f"https://dead/{n}": [None] for n in range(40)}, "https://h/1": [200]})
    told: list[tuple[int, int]] = []
    verdicts = await ask_many(player, refs_of(player), None, "https://page", Pacer(0.0),
                              on_recheck=lambda current, total: told.append((current, total)))
    dead = [ref for ref in refs_of(player) if "dead" in ref.url]
    assert all(outcome_of(verdicts[ref], ref, player.label).state.value == "not_obtained" for ref in dead)
    assert sum(player.asked[ref.url] for ref in dead) == 40 * attempts + attempts
    assert player.asked["https://h/1"] == 1 and told == [(0, 40), (40, 40)]


async def test_two_causes_are_two_questions_each_answered_by_one_video():
    """The videos a host refused with 500 and the ones another host did not answer are looked at apart: the first were
    the load and are all asked again, the second are down and keep their answer."""
    attempts = policy.DEFAULT.attempts
    player = Hosted({"https://h/1": [500] * attempts + [200], "https://h/2": [500] * attempts + [200],
                     "https://dead/1": [None], "https://dead/2": [None]})
    verdicts = await ask_many(player, refs_of(player), None, "https://page", Pacer(0.0))
    kinds = {ref.url: verdicts[ref].reading.kind for ref in refs_of(player)}
    assert kinds == {"https://h/1": Kind.PLAYABLE, "https://h/2": Kind.PLAYABLE,
                     "https://dead/1": Kind.NO_ANSWER, "https://dead/2": Kind.NO_ANSWER}
    assert player.asked["https://dead/1"] + player.asked["https://dead/2"] == 2 * attempts + attempts
