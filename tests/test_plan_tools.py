"""The tools of the plan on a real series (tools/checks/plan_real.py, plan_on_asset.py), proved on the stand of the whole
application: a series synced to its end is a settled series, so the tools read from its database the same layout the
service wrote and a plan with no step — the asset is the table the planner keeps (plan_assets), not a table of the old path."""
import os
import sys

import pytest

from app_stand import AppStand

CHECKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "checks")
sys.path.insert(0, CHECKS)

import plan_on_asset  # noqa: E402
import plan_real  # noqa: E402


@pytest.fixture
async def stand(tmp_path):
    made = AppStand(str(tmp_path))
    await made.start()
    yield made
    await made.stop()
    made.close()


async def test_a_settled_series_comes_out_of_the_tools_with_its_layout_its_asset_and_no_step(stand):
    stand.site.release("ref", "AniStar")
    stand.site.release("dub", "AnimeVost")
    for number in (1, 2):
        stand.site.episode("ref", number, height=720)
        stand.site.episode("dub", number, height=480)
    await stand.configure(["AniStar", "AnimeVost"])
    series_id = await stand.series("Сериал", ["ref", "dub"])
    await stand.sync(series_id)
    await stand.bus.settled()

    real = plan_real.RealSeries(stand.db_path)
    inputs, _skipped, info = await plan_real.inputs_of(real, series_id)
    assert plan_real.same_layout(*(await real.layout(series_id))[:1], info["stored"].rows) == []
    assert len(inputs.containers) == 2 and {c.video.snapshot.group for c in inputs.containers} == {"AniStar"}
    assert all([t.group for t in c.tracks] == ["AnimeVost"] for c in inputs.containers)

    report, counts = await plan_on_asset.series_report(real, series_id)
    assert counts == {"builds": 0, "ops": 0, "renames": 0, "removes": 0, "hides": 0, "missing": 0}, report
