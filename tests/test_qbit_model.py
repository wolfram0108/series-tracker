"""The model of qBittorrent answers as the real client was measured to answer (tests/samples/qbit/update_5_2_1.json): the
same update scenario, run by the real client of the service (QbtClient) over real HTTP, gives the same observations;
then what the model adds for the stand — a swarm that seeds a release, a download held until the test's word, a magnet
whose metadata comes from the swarm."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app_stand import Served
from modules.torrents.qbt_client import QbtClient
from qbit_model import QbitModel, UpdateScenario, magnet_of, make_torrent, payload

SAMPLE = Path(__file__).resolve().parent / "samples" / "qbit" / "update_5_2_1.json"


@pytest.fixture
async def qbit():
    model = QbitModel()
    served = Served(model.app)
    client = QbtClient(served.url, model.username, model.password, retries=1)
    await client.login()
    yield model, client
    await client.close()
    served.close()


def _shape(rows: list[dict], first_disk: dict[str, int]) -> list[dict]:
    shaped = []
    for o in rows:
        if o["scenario"] == "env":
            continue
        i = o["info"]
        shaped.append({
            "scenario": o["scenario"], "step": o["step"],
            "info": {"state": i["state"], "progress": round(i["progress"], 4), "amount_left": i["amount_left"]} if i else None,
            "files": [{"name": f["name"], "progress": round(f["progress"], 4), "priority": f["priority"],
                       "piece_range": f["piece_range"]} for f in o["files"]] if o["files"] else None,
            "pieces": o["pieces"],
            "disk": [{"path": d["path"], "dir": d["dir"], "size": d["size"]} for d in o["disk"]],
            "written": sorted(d["path"] for d in o["disk"] if not d["dir"] and d["path"] in first_disk
                              and first_disk[d["path"]] != d["mtime_ns"])})
    return shaped


async def test_the_update_scenario_gives_what_the_real_client_gave(qbit, tmp_path):
    model, client = qbit
    rows: list[dict] = []
    scenario = UpdateScenario(client, tmp_path / "probe", str(tmp_path / "probe"),
                              lambda scenario, step, **fields: rows.append({"scenario": scenario, "step": step, **fields}))
    await scenario.run()
    first = next(r for r in rows if r["scenario"] == "A")
    got = _shape(rows, {d["path"]: d["mtime_ns"] for d in first["disk"] if not d["dir"]})
    want = json.loads(SAMPLE.read_text(encoding="utf-8"))["observations"]
    assert [(o["scenario"], o["step"]) for o in got] == [(o["scenario"], o["step"]) for o in want]
    for mine, real in zip(got, want):
        for field in real.get("stale", []):
            # The real client reports these of before a priority change until the torrent runs; the model is current.
            mine["info"].pop(field)
            real["info"].pop(field)
        real.pop("stale", None)
        assert mine == real, (real["scenario"], real["step"])


async def test_a_seeded_release_downloads_on_start_into_its_folder(qbit, tmp_path):
    model, client = qbit
    files = [("Show/E01.mkv", payload("a", 300_000)), ("Show/Sound/E01.mka", payload("b", 70_000))]
    meta, h = model.seed(files)
    assert await client.add_torrent_file(meta, str(tmp_path), paused=False) == "ok"
    [info] = await client.torrents_info([h])
    assert (info["state"], info["progress"]) == ("stalledUP", 1.0)
    for path, content in files:
        assert (tmp_path / path).read_bytes() == content


async def test_a_held_download_ends_on_the_word_of_the_test(qbit, tmp_path):
    model, client = qbit
    files = [("Show/E01.mkv", payload("a", 300_000))]
    meta, h = model.seed(files, hold=True)
    await client.add_torrent_file(meta, str(tmp_path), paused=False)
    [info] = await client.torrents_info([h])
    assert (info["state"], info["progress"]) == ("downloading", 0.0)
    assert not (tmp_path / "Show").exists()
    model.deliver(h)
    [info] = await client.torrents_info([h])
    assert info["state"] == "stalledUP"
    assert (tmp_path / "Show/E01.mkv").read_bytes() == files[0][1]


async def test_priority_zero_keeps_a_file_off_the_disk(qbit, tmp_path):
    model, client = qbit
    files = [("Show/E01.mkv", payload("a", 300_000)), ("Show/E02.mkv", payload("b", 300_000))]
    meta, h = model.seed(files)
    await client.add_torrent_file(meta, str(tmp_path), paused=True)
    await client.set_file_priority(h, [1], 0)
    await client.resume([h])
    assert (tmp_path / "Show/E01.mkv").read_bytes() == files[0][1]
    assert not (tmp_path / "Show/E02.mkv").exists() and not (tmp_path / "Show/E02.mkv.!qB").exists()
    [info] = await client.torrents_info([h])
    assert info["state"] == "stalledUP"


async def test_a_magnet_gets_its_files_from_the_swarm_while_it_runs(qbit, tmp_path):
    model, client = qbit
    files = [("Show/E01.mkv", payload("a", 300_000))]
    _meta, h = make_torrent(files)
    await client.add_magnet(magnet_of(h), str(tmp_path), paused=False)
    [info] = await client.torrents_info([h])
    assert info["state"] == "metaDL"
    assert await client.torrent_files(h) == []
    model.seed(files, hold=True)
    assert [f["name"] for f in await client.torrent_files(h)] == ["Show/E01.mkv"]


async def test_a_second_add_of_the_same_release_is_a_conflict(qbit, tmp_path):
    model, client = qbit
    meta, _h = model.seed([("Show/E01.mkv", payload("a", 1000))])
    assert await client.add_torrent_file(meta, str(tmp_path)) == "ok"
    assert await client.add_torrent_file(meta, str(tmp_path)) == "fails"
