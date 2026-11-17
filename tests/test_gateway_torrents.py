"""The gateway points of a torrent source (stage 15): each is one owner's answer as it is — what the page lists (sources),
the releases and their life (torrents), the editor (unpack); a refusal of an owner is a refusal of the point."""
from core.contracts import NotAllowed
from core.reasons import reason
from gateway_fakes import serving

URL = "https://kinozal.tv/details.php?id=7"


async def test_every_point_asks_its_owner_with_the_body_as_it_came(tmp_path):
    answers = {"sources.releases.get": {"url": URL, "site": "kinozal", "releases": []},
               "torrents.releases.get": {"releases": []}, "torrents.release.download": {"release": "page"},
               "torrents.release.check": {"releases": ["page"]}, "torrents.release.watch": {"watched": False},
               "unpack.editor.get": {"url": URL, "view": {}}, "unpack.assign": None, "unpack.unassign": None,
               "unpack.folder.set": None, "unpack.folder.delete": None}
    async with serving(tmp_path, answers) as (client, owners, _, _):
        for point, body in (("page", {"url": URL}), ("releases", {"url": URL}),
                            ("download", {"url": URL, "release": "page"}), ("check", {"url": URL}),
                            ("watch", {"url": URL, "release": "page", "watched": False}), ("editor", {"url": URL}),
                            ("editor/assign", {"url": URL, "tab": "files", "items": ["page/E01.mkv"], "season": 1, "episode": 3}),
                            ("editor/unassign", {"url": URL, "tab": "files", "items": ["page/E01.mkv"]}),
                            ("editor/folder", {"url": URL, "kind": "voice", "name": "JAM", "language": "rus"}),
                            ("editor/folder/delete", {"url": URL, "kind": "voice", "name": "JAM"})):
            assert (await client.post(f"/api/torrent/{point}", json=body)).status_code == 200, point
    assert owners.asked("torrents.release.download") == [{"url": URL, "release": "page"}]
    assert owners.asked("unpack.assign") == [{"url": URL, "tab": "files", "items": ["page/E01.mkv"], "season": 1, "episode": 3,
                                              "episode_last": None, "folder": None, "in_order": False}]
    assert owners.asked("unpack.folder.set") == [{"url": URL, "kind": "voice", "name": "JAM", "language": "rus",
                                                  "subtitle_type": ""}]


async def test_a_refusal_of_the_owner_is_a_conflict_with_its_reason_and_a_malformed_body_is_refused_before(tmp_path):
    answers = {"torrents.release.download": NotAllowed(reason("staging_dir_not_set"))}
    async with serving(tmp_path, answers) as (client, owners, _, _):
        refused = await client.post("/api/torrent/download", json={"url": URL, "release": "page"})
        malformed = await client.post("/api/torrent/editor/assign", json={"url": URL, "tab": "other", "items": ["x"]})
        # The hand places, it never leaves out: a track with no folder, a file with no episode is no placement.
        nowhere = [await client.post("/api/torrent/editor/assign", json={"url": URL, "tab": tab, "items": ["x"]})
                   for tab in ("streams", "files")]
    assert refused.status_code == 409 and "staging_dir_not_set" in refused.text
    assert malformed.status_code == 422 and [each.status_code for each in nowhere] == [422, 422]
    assert owners.asked("unpack.assign") == []
