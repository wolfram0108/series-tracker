import pytest

from modules.sources import policy
from modules.sources.players.contract import Answer, Kind, PlayerRef, Reading
from modules.sources.policy import Budget, Pacer, is_retryable, settle, status_is_transient

REF = PlayerRef("fake", "https://player.test/1")
READINGS = {
    "video": Reading(Kind.PLAYABLE, 200, (1080,)), "gone": Reading(Kind.NOT_FOUND, 200),
    "closed": Reading(Kind.RESTRICTED, 200), "empty": Reading(Kind.NO_FILE, 200), "shell": Reading(Kind.NOT_PLAYER, 200),
}


class FakePlayer:
    """Answers from a script; the last answer repeats."""
    name, series_scope = "fake", False

    def __init__(self, *answers: Answer) -> None:
        self.answers, self.fetched = list(answers), 0

    def find(self, text, page_url):
        return REF

    async def fetch(self, ref, http, page_url):
        self.fetched += 1
        return self.answers[min(self.fetched - 1, len(self.answers) - 1)]

    def read(self, answer):
        if answer.status is None:
            return Reading(Kind.NO_ANSWER)
        if answer.status != 200:
            return Reading(Kind.HTTP_ERROR, answer.status)
        return READINGS[answer.text]


class FakeTime:
    """A clock that only moves when something sleeps, so no test waits."""

    def __init__(self) -> None:
        self.now, self.slept = 0.0, []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def said(text: str, status: int = 200, retry_after: float | None = None) -> Answer:
    return Answer(status, text, retry_after)


async def run(player: FakePlayer, budget: Budget | None = None, pacer: Pacer | None = None):
    time = FakeTime()
    verdict = await settle(player, REF, None, "https://page.test", budget, pacer=pacer, clock=time.clock, sleep=time.sleep)
    return verdict, time


@pytest.mark.parametrize("text,kind", [("video", Kind.PLAYABLE), ("gone", Kind.NOT_FOUND), ("closed", Kind.RESTRICTED)])
@pytest.mark.asyncio
async def test_a_final_answer_is_taken_at_once(text, kind):
    verdict, time = await run(FakePlayer(said(text)))

    assert (verdict.reading.kind, verdict.attempts, verdict.exhausted, time.slept) == (kind, 1, False, [])


@pytest.mark.asyncio
async def test_an_unsure_answer_is_asked_again_after_the_scheduled_pause():
    verdict, time = await run(FakePlayer(said("shell"), said("video")), Budget())

    assert (verdict.reading.kind, verdict.attempts, verdict.exhausted, time.slept) == (Kind.PLAYABLE, 2, False, [2.0])


@pytest.mark.asyncio
async def test_pauses_follow_the_schedule_and_what_stays_unsure_is_not_obtained():
    player = FakePlayer(said("shell"))

    verdict, time = await run(player, Budget())

    assert (verdict.reading.kind, verdict.attempts, verdict.exhausted) == (Kind.NOT_PLAYER, 3, True)
    assert (time.slept, player.fetched) == ([2.0, 6.0], 3)


@pytest.mark.parametrize("unsure", [said("empty"), said("shell"), Answer(None, error="timeout"), said("", 429), said("", 503),
                                    said("", 403)])
@pytest.mark.asyncio
async def test_every_unsure_answer_is_asked_again(unsure):
    verdict, _ = await run(FakePlayer(unsure, said("video")))

    assert (verdict.reading.kind, verdict.attempts) == (Kind.PLAYABLE, 2)


@pytest.mark.parametrize("status", [301, 404, 410])
@pytest.mark.asyncio
async def test_a_final_http_status_is_not_asked_again(status):
    verdict, time = await run(FakePlayer(said("", status)))

    assert (verdict.attempts, verdict.exhausted, time.slept) == (1, False, [])


@pytest.mark.asyncio
async def test_the_wait_a_host_asks_for_outlasts_the_scheduled_pause():
    verdict, time = await run(FakePlayer(said("", 429, retry_after=10.0), said("video")))

    assert (verdict.attempts, time.slept) == (2, [10.0])


@pytest.mark.asyncio
async def test_the_time_limit_stops_the_asking_before_the_attempts_run_out():
    player = FakePlayer(said("shell"))

    verdict, time = await run(player, Budget(attempts=5, pauses=(5.0,), limit_s=8.0))

    assert (verdict.attempts, verdict.exhausted, time.slept) == (2, True, [5.0])


@pytest.mark.asyncio
async def test_the_default_budget_is_read_when_asking_not_when_importing(monkeypatch):
    monkeypatch.setattr(policy, "DEFAULT", Budget(attempts=1))

    verdict, time = await run(FakePlayer(said("shell")))

    assert (verdict.attempts, verdict.exhausted, time.slept) == (1, True, [])


@pytest.mark.parametrize("status,transient", [(403, True), (408, True), (425, True), (429, True), (500, True), (503, True),
                                              (200, False), (301, False), (404, False), (410, False)])
def test_which_statuses_can_pass(status, transient):
    assert status_is_transient(status) is transient


def test_only_the_unsure_kinds_and_transient_statuses_are_asked_again():
    asked_again = {kind for kind in Kind if is_retryable(Reading(kind))}

    assert asked_again == {Kind.NOT_PLAYER, Kind.NO_FILE, Kind.NO_ANSWER}
    assert is_retryable(Reading(Kind.HTTP_ERROR, 429)) and not is_retryable(Reading(Kind.HTTP_ERROR, 410))


@pytest.mark.asyncio
async def test_a_pacer_spaces_the_requests_to_a_host():
    time = FakeTime()
    pacer = Pacer(1.0, clock=time.clock, sleep=time.sleep)

    for _ in range(3):
        await pacer.wait()

    assert time.slept == [1.0, 1.0]


@pytest.mark.asyncio
async def test_every_ask_goes_through_the_pacer():
    class Counting(Pacer):
        waited = 0

        async def wait(self):
            Counting.waited += 1

    verdict, _ = await run(FakePlayer(said("shell"), said("video")), pacer=Counting(0.0))

    assert (verdict.attempts, Counting.waited) == (2, 2)


def scripted(*answers: Answer):
    """A request that answers from a script; the last answer repeats."""
    given = []

    async def call() -> Answer:
        given.append(1)
        return answers[min(len(given) - 1, len(answers) - 1)]
    return call, given


@pytest.mark.asyncio
async def test_a_page_or_catalog_is_asked_again_while_the_host_is_busy_and_then_taken_as_it_is():
    fake = FakeTime()
    call, given = scripted(Answer(None, error="ReadTimeout"), Answer(503), Answer(200, "page"))
    answer = await policy.ask(call, Budget(attempts=3, pauses=(1.0, 2.0), limit_s=30.0), clock=fake.clock, sleep=fake.sleep)
    assert (answer.status, answer.text, len(given), fake.slept) == (200, "page", 3, [1.0, 2.0])


@pytest.mark.asyncio
async def test_a_refusal_that_is_not_the_host_being_busy_is_not_asked_again():
    call, given = scripted(Answer(404))
    answer = await policy.ask(call, Budget(attempts=3, pauses=(0.0,), limit_s=30.0))
    assert (answer.status, len(given)) == (404, 1)


@pytest.mark.asyncio
async def test_the_last_answer_comes_back_when_the_host_stays_busy():
    call, given = scripted(Answer(429, retry_after=7.0), Answer(503))
    fake = FakeTime()
    answer = await policy.ask(call, Budget(attempts=2, pauses=(1.0,), limit_s=30.0), clock=fake.clock, sleep=fake.sleep)
    assert (answer.status, len(given), fake.slept) == (503, 2, [7.0])
