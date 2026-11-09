"""A title of a MacCMS collection site (Jisu) as the source of the reference, in a browser (tests/ui_stand.py): the user
adds the title by its page in the window of a new series beside a source of a voice-over, makes the rule of its labels
(«第N集»), and syncs. The frame of each episode is measured on the picture, once: neither the next sync nor a restart of
the service measures it again."""
import asyncio
import json
import os
import subprocess

import pytest
from playwright.async_api import expect

from ui_stand import SERIES, SERIES_EN, lines_of, ui  # noqa: F401 - the fixture

pytestmark = pytest.mark.ui

LABEL = [{"type": "text", "value": "第"}, {"type": "number"}, {"type": "text", "value": "集"}]
ORIGINAL = "Оригинал"


def frame_height(path: str) -> int:
    probed = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height", "-of",
                             "json", path], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(probed.stdout)["streams"][0]["height"]


async def test_a_title_of_jisu_is_the_reference_its_frames_measured_once_through_syncs_and_a_restart(ui):
    title = ui.site.title(4794)
    ui.site.release("dub", "AnimeVost")
    for episode in (1, 2):
        await asyncio.to_thread(ui.site.hls_episode, 4794, episode, height=1080)
        ui.site.episode("dub", episode, height=480)

    await ui.first_run()
    await ui.set_up([ORIGINAL, "AnimeVost"])
    await ui.profile("серии Jisu", [{"name": "серия", "if": LABEL,
                                     "then": [("extract_single", LABEL), ("assign_voiceover", ORIGINAL)]}])
    await ui.profile("серии AnimeVost", lines_of("AnimeVost"))
    series_id = await ui.add_series(SERIES, SERIES_EN, [(title.url, "серии Jisu"),
                                                        (ui.site.releases["dub"].url, "серии AnimeVost")])
    await ui.open_output()
    await ui.press_sync(series_id)

    await expect(ui.page.locator(".episode-card.is-ready")).to_have_count(2)
    await ui.shot("m1-jisu-reference")
    view = await ui.check(series_id)
    # The picture of every file is the one of Jisu: its frame, measured on the picture, beats the label of the voice-over.
    assert [frame_height(os.path.join(ui.library, asset["name"])) for asset in view["assets"]] == [1080, 1080]
    measured = list(ui.site.heads)
    assert sorted(measured) == ["jisu-4794-ep1/seg0.ts", "jisu-4794-ep2/seg0.ts"]

    ui.later()
    await ui.press_sync(series_id)
    await ui.restart()
    ui.later()
    await ui.press_sync(series_id)

    await expect(ui.page.locator(".episode-card.is-ready")).to_have_count(2)
    assert ui.site.heads == measured  # read again twice, once after a restart: nothing measured anew
    await ui.check(series_id)
