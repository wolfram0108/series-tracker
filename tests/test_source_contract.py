"""The contract of a source (contracts/source_contract.md), kept by the whole application on its stand
(tests/app_stand.py): a table of every kind of source the stand models is read the way the windows read it, and what its
rows give the consumers is checked: every row has the frame of its own episode (the first criterion of the order of
variants; a row without one cannot reach the constructor, and a source that leaves one is not fit), never the frame of
another row; and the player of every row, as every player of the registry, has its place and its words in the order of sources."""
import asyncio
import re

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


async def test_every_row_of_every_kind_of_source_has_the_frame_of_its_own_episode_and_a_player_the_order_places(stand):
    # Every kind of source the stand models, each with episodes in different frames: a frame carried from one row to
    # another shows as a row whose frame is not the one of its own episode.
    stand.site.release("ref", "AniStar")
    stand.site.episode("ref", 1, height=720)
    stand.site.episode("ref", 2, height=480)
    stand.site.title(4794)
    await asyncio.to_thread(stand.site.hls_episode, 4794, 1, height=1080)
    await asyncio.to_thread(stand.site.hls_episode, 4794, 2, height=720)
    await asyncio.to_thread(stand.site.app_card, 219017, "万界独尊", {1: 1080, 2: 2160})
    await asyncio.to_thread(stand.site.app_card, 219018, "师兄啊师兄", {1: 720, 2: 1080}, files=True)
    truth = {stand.site.releases["ref"].url: {1: 720, 2: 480}, stand.site.titles[4794].url: {1: 1080, 2: 720},
             "app://banri/219017": {1: 1080, 2: 2160}, "app://banri/219018": {1: 720, 2: 1080}}
    # Every other protocol of the applications the service reads: each of its own model, its line admitted.
    for number, protocol in enumerate(("appdrama", "appget", "gulu", "yunduo", "appmn"), start=500):
        frames = {1: 720, 2: 1080} if number % 2 else {1: 1080, 2: 720}
        truth[await asyncio.to_thread(stand.site.protocol_card, protocol, number, f"万界独尊{number}", frames)] = frames

    tables = await tables_of(stand, list(truth))
    order = await stand.call("GET", "/api/voices/settings")
    placed = {fold_name(name) for name in order["priority"]}
    sources = stand.module("sources")

    for (url, frames), table in zip(truth.items(), tables):
        rows = table["rows"]
        got = {}
        for row in rows:
            # Condition 1: a row with no frame of its own episode cannot reach the constructor: the source is not fit.
            assert row["state"] == "playable" and row["heights"], (url, row["key"], row["state"], row.get("reason"))
            got[int(re.search(r"\d+", row["text"]).group())] = max(row["heights"])
            # Obligation 3: the user can put the player of the row above or below the others, by its words.
            assert fold_name(row["player"]) in placed, (url, row["player"], order["priority"])
            assert order["player_labels"].get(row["player"]), (url, row["player"])
        # Condition 2: the frame of each row is the frame of its own episode, never one carried from another row.
        assert got == frames, (url, got)
    assert order["player_labels"]["line:BLU"] == "BLU"  # a line of an application is named by its code
    for code in ("DRAMA", "MOLI", "GULU", "YUNDUO", "MINUO"):
        assert order["player_labels"][f"line:{code}"] == code
    # Obligation 3 for every player a row can be of, whether a table of the stand holds one or not.
    for player in sources.players:
        if not getattr(player, "lines", False):
            assert fold_name(player.name) in placed, (player.name, order["priority"])
