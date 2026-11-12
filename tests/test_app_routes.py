"""A line of an application that another one parses when its own parser says no (apps/routes.json, AppPlayer.through):
the own parser first, the other application's after its refusal and straight for the term, the own one asked again after
it; a passing failure is no refusal; the routes of the registry and the wiring of the module."""
from __future__ import annotations

import json

import pytest

from core.db import Database
from core import Bus
from modules.sources.apps import AppError, AppProfile, AppStream
from modules.sources.apps.lines import LineRegistry, routes_of
from modules.sources.module import SourcesModule
from modules.sources.players.app_lines import QUOTA_RECHECK_S, AppPlayer
from modules.sources.players.contract import Answer, Kind, PlayerRef, Reading
from schema_db import migrated


class Fake:
    """An application client that answers each token as the test says: an address or the error to raise."""

    def __init__(self, name: str, answers: dict[str, object]) -> None:
        self.profile = AppProfile(name, name, "fake", {})
        self.answers, self.calls = answers, []

    async def resolve(self, line: str, token: str, card: str = "") -> AppStream:
        self.calls.append((line, token, card))
        answer = self.answers[token]
        if isinstance(answer, Exception):
            raise answer
        return AppStream(str(answer))


def world(own: dict, other: dict, now: list):
    own_client, other_client = Fake("xiaozhu", own), Fake("mtq", other)
    player = AppPlayer(own_client, registry=LineRegistry(frozenset({"Z"})), clock=lambda: now[0])
    player.through = {"Z": (other_client, "Z")}
    return player, own_client, other_client


async def test_a_line_whose_own_parser_refused_goes_to_the_other_application_and_the_own_one_is_asked_again_later():
    now = [0.0]
    refused = AppError('{"code":"app_status","status":400}')
    player, own, other = world({"a": refused, "b": "https://own/b", "c": refused}, {"a": "https://other/a", "c": "https://other/c"}, now)
    assert (await player.address("Z", "a", "388927")).address == "https://other/a"
    # The other application is asked without the card of the first: the card is the first's own.
    assert other.calls == [("Z", "a", "")] and own.calls == [("Z", "a", "388927")]
    # Within the term the asks of the line go straight to it: the refusing parser is not asked for each episode.
    own.answers["c"] = refused
    assert (await player.address("Z", "c", "388927")).address == "https://other/c" and len(own.calls) == 1
    # After the term the own parser is asked again, and its answer is the line's.
    now[0] += QUOTA_RECHECK_S + 1
    assert (await player.address("Z", "b", "388927")).address == "https://own/b" and len(own.calls) == 2


async def test_a_passing_failure_is_no_refusal_and_a_line_without_a_route_keeps_its_own_error():
    now = [0.0]
    passing = AppError('{"code":"app_unreachable"}', True)
    player, own, other = world({"a": passing}, {"a": "https://other/a"}, now)
    with pytest.raises(AppError) as raised:
        await player.address("Z", "a", "1")
    assert raised.value is passing and other.calls == []
    # The other application refusing as well: its refusal is the answer.
    player, _own, other = world({"a": AppError("no")}, {"a": AppError("other said no")}, now)
    with pytest.raises(AppError) as both:
        await player.address("Z", "a", "1")
    assert both.value.reason == "other said no"
    bare = AppPlayer(Fake("xiaozhu", {"a": AppError("own no")}), registry=LineRegistry(frozenset({"Z"})), clock=lambda: now[0])
    with pytest.raises(AppError) as alone:
        await bare.address("Z", "a", "1")
    assert alone.value.reason == "own no"


async def test_an_address_of_the_own_parser_that_serves_a_notice_goes_to_the_other_application_within_the_same_ask():
    """The own parser says success and names a file; the host serves the notice of its limit in place of the episode. The
    ask is made again through the other application, and the asks of the line that follow go straight to it."""
    now = [0.0]
    player, own, other = world({"a": "https://own/notice.mp4", "b": "https://own/b.mp4"},
                               {"a": "https://other/a.m3u8", "b": "https://other/b.m3u8"}, now)

    async def served(_http, address, _headers, _measure):
        if "own" in address:
            return Answer(200, reading=Reading(Kind.RESTRICTED, 200, message="the host served its notice"))
        return Answer(200, reading=Reading(Kind.PLAYABLE, 200, (2160,)))

    player._served = served
    first = await player.fetch(PlayerRef("line:Z", "app://xiaozhu/1#line=Z&item=a"), None, "")
    assert player.read(first).kind is Kind.PLAYABLE and own.calls == [("Z", "a", "1")] and other.calls == [("Z", "a", "")]
    second = await player.fetch(PlayerRef("line:Z", "app://xiaozhu/1#line=Z&item=b"), None, "")
    assert player.read(second).kind is Kind.PLAYABLE and len(own.calls) == 1 and other.calls[-1] == ("Z", "b", "")


def test_the_routes_of_the_registry_name_the_application_that_parses_a_line_and_its_line_there(tmp_path):
    assert routes_of("xiaozhu") == {"zijianm3u8": ("mtq", "zijianm3u8")} == routes_of("moli")
    assert routes_of("gulu") == {"CO4K": ("banri", "CO4K")} and routes_of("banri") == {}
    path = tmp_path / "routes.json"
    path.write_text(json.dumps({"a": {"X": {"through": "b", "line": "Y"}}}), encoding="utf-8")
    assert routes_of("a", str(path)) == {"X": ("b", "Y")} and routes_of("a", str(tmp_path / "none.json")) == {}


def profile(name: str, protocol: str, **settings) -> AppProfile:
    return AppProfile(name, name, protocol, {"label": name, "protocol": protocol, **settings})


def test_the_module_gives_a_line_its_route_only_where_the_other_application_is_configured(tmp_path):
    db = Database(str(migrated(tmp_path / "s.db")))
    mtq = profile("mtq", "appv7", api="https://m.model/v7", package="p", build_time="1", salt="s", version="1",
                  version_number="1", platform_version="13")
    xiaozhu = profile("xiaozhu", "appv7", api="https://x.model/v7", package="p", build_time="1", salt="s", version="1",
                      version_number="1", platform_version="13")
    both = SourcesModule(Bus(), db, torrent_cache_dir=str(tmp_path / "c1"), apps=[xiaozhu, mtq])
    routed = next(each for each in both.apps if each.name == "xiaozhu")
    assert list(routed.through) == ["zijianm3u8"] and routed.through["zijianm3u8"][1] == "zijianm3u8"
    alone = SourcesModule(Bus(), db, torrent_cache_dir=str(tmp_path / "c2"), apps=[xiaozhu])
    assert alone.apps[0].through == {}
