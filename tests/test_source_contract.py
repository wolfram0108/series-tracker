"""The contract of a source (contracts/source_contract.md), kept by the whole application on its stand
(tests/app_stand.py): a table of every kind of source the stand models is read the way the windows read it, and what its
rows give the consumers is checked — a row a player confirmed has the frame of its own video (the first criterion of the
order of variants), never the frame of another row,
and the player of every row, as every player of the registry, has its place and its words in the order of sources."""
import asyncio

import pytest

from app_stand import STAND_LIMIT_S, AppStand
from core.voice_names import fold_name


@pytest.fixture
async def stand(tmp_path):
    made = AppStand(str(tmp_path))
    await made.start()
    yield made
    await made.stop()
    made.close()


async def tables_of(stand: AppStand, urls: list[str]) -> list[dict]:
    """The table of each address as a window gets it: read now, then taken from the store."""
    sources = stand.module("sources")
    keys = [(await sources.request("sources.site.resolve", {"url": url}))["canonical_url"] for url in urls]
    ended = stand.bus.subscribe("sources.table.ready", "sources.table.failed")
    for url in urls:
        await stand.call("POST", "/api/sources/read", {"url": url})
    waiting = set(keys)
    while waiting:
        packet = await asyncio.wait_for(ended.queue.get(), STAND_LIMIT_S)
        assert packet.topic == "sources.table.ready", packet.payload
        waiting.discard(packet.payload["url"])
    tables = []
    for key in keys:
        got = await sources.request("sources.table.get", {"url": key})
        assert got["state"] == "ready", got
        tables.append(got["table"])
    return tables


async def test_every_row_of_every_kind_of_source_has_a_frame_and_a_player_the_order_of_sources_places(stand):
    stand.site.release("ref", "AniStar")
    stand.site.episode("ref", 1, height=720)
    stand.site.title(4794)
    await asyncio.to_thread(stand.site.hls_episode, 4794, 1, height=1080)
    # Episodes of one line in different frames: a frame is of the row whose video it was measured on, of no other.
    await asyncio.to_thread(stand.site.app_card, 219017, "万界独尊", {1: 1080, 2: 2160})
    urls = [stand.site.releases["ref"].url, stand.site.titles[4794].url, "app://banri/219017"]

    tables = await tables_of(stand, urls)
    order = await stand.call("GET", "/api/voices/settings")
    placed = {fold_name(name) for name in order["priority"]}
    sources = stand.module("sources")

    for url, table in zip(urls, tables):
        rows = table["rows"]
        assert rows, url
        for row in rows:
            # Obligation 2: the frame of a confirmed row ranks the variants before anything else.
            assert row["state"] != "playable" or row["heights"], (url, row["key"], "a confirmed row without a frame")
            # Obligation 3: the user can put the player of the row above or below the others, by its words.
            assert fold_name(row["player"]) in placed, (url, row["player"], order["priority"])
            assert order["player_labels"].get(row["player"]), (url, row["player"])
    assert {row["player"] for row in tables[2]["rows"]} == {"banri:BLU"}
    app = {row["text"]: (row["state"], row["heights"]) for row in tables[2]["rows"]}
    assert app == {"第1集": ("not_obtained", []), "第2集": ("playable", [2160])}, app
    assert order["player_labels"]["banri:BLU"] == "半日 · 蓝光"
    # Obligation 3 for every player a row can be of, whether a table of the stand holds one or not.
    for player in sources.players:
        if not getattr(player, "lines", False):
            assert fold_name(player.name) in placed, (player.name, order["priority"])
