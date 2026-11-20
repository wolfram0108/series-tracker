"""A real series as the tables of the planner (core/plan), read from a copy of the service's database.

The rows come out of the real constructor: the stored tables of the pages go through the rules of their profiles
(modules/rules: compile_profile, process_title, result_of, settle_parts) and the layout (calc/layout/project),
with the groups of voice-overs of the settings — the same functions the service runs. Other rules can be given in place of
the stored ones: a situation «this rule is changed» is the real constructor run with them. The asset (files and tracks)
is the table the planner keeps (plan_assets), read against the rows as the planner reads it; the choices come from the
settings and the levers of the output.

Nothing is written: the database is copied first (sqlite backup of a read-only connection), and every repository reads
the copy."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
import os
import sqlite3
import subprocess
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from core.contracts import (CompositionGetRequest, CompositionRow, RulesApplyReply,  # noqa: E402
                            SeriesSource, SourceType)
from core.db import Database  # noqa: E402
from core.plan import inputs as plan_inputs  # noqa: E402
from core.plan.files import container_of  # noqa: E402
from core.plan.tables import Container, Inputs, OnlineRow  # noqa: E402
from modules.catalog.repository import CatalogRepository  # noqa: E402
from calc.layout import project  # noqa: E402
from calc.layout.reply import layout_of  # noqa: E402
from modules.composition.repository import CompositionRepository  # noqa: E402
from modules.rules.engine import compile_profile, process_title  # noqa: E402
from modules.rules.module import result_of  # noqa: E402
from modules.rules.numbering import settle_parts  # noqa: E402
from modules.rules.repository import RulesRepository  # noqa: E402
from modules.settings.repository import SettingsRepository  # noqa: E402
from modules.sources.repository import SourcesRepository  # noqa: E402
from modules.voices import editor  # noqa: E402
from modules.voices.settings import PREFIX, parse  # noqa: E402

PROD_DB = os.path.join(ROOT, "app.db")


def copy_db(target: str) -> str:
    """A copy of the service's database, made through a read-only connection and brought to the head of the migrations of
    this code (the copy only: the service's database may wait for its update)."""
    os.makedirs(os.path.dirname(target), exist_ok=True)
    source = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    copy = sqlite3.connect(target)
    source.backup(copy)
    copy.close()
    source.close()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True, capture_output=True,
                   env={**os.environ, "ST_DB_URL": f"sqlite:///{os.path.abspath(target)}"}, timeout=120)
    return target


class RealSeries:
    """The readers of one copy of the database."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.db = Database(path)
        self.catalog = CatalogRepository(self.db)
        self.composition = CompositionRepository(self.db)
        self.sources = SourcesRepository(self.db)
        self.rules = RulesRepository(self.db)
        self.settings = SettingsRepository(self.db)

    async def rules_of(self, profile_id: int) -> list[dict]:
        return await self.rules.get_rules(profile_id)

    async def layout(self, series_id: int, rules: dict[int, list[dict]] | None = None) -> tuple[list[CompositionRow], dict]:
        """The rows of the layout as the real constructor makes them now; `rules` replaces the stored rules of profiles."""
        card = await self.catalog.get_series(series_id)
        stored = await self.composition.stored(series_id)
        urls = {each.source_id: each.table_url for each in stored.sources}
        inputs = []
        for each in card.get("sources", []):
            if each.get("source_type") == SourceType.TORRENT:
                continue
            source = SeriesSource.model_validate(each)
            url = urls.get(source.id)
            table = (await self.sources.stored(url)).table if url else None
            answer = None
            if table is not None and source.parser_profile_id is not None:
                lines = project.lines_for(source, table)
                raw = (rules or {}).get(source.parser_profile_id)
                profile = compile_profile(source.parser_profile_id,
                                          raw if raw is not None else await self.rules_of(source.parser_profile_id))
                results = settle_parts(lines, {line.key: result_of(process_title(profile, line.text), card.get("season"))
                                               for line in lines})
                answer = RulesApplyReply(results=results, invalid_rules=profile.invalid_rules)
            inputs.append(project.SourceInput(source, table, answer, None, url))
        voices = parse(await self.settings.by_prefix(PREFIX))
        names = project.voice_names(inputs)
        groups = {name: voices.voices.group_of(name) for name in names}
        made = project.project(inputs, groups, stored.numbering)
        places = {each.source_id: " / ".join(each.summary.place or []) or (each.table_url or "") for each in stored.sources}
        return made.rows, {"card": card, "stored": stored, "places": places, "voices": voices}


def asset_of(path: str, series_id: int, online: list[OnlineRow]) -> list[Container]:
    """The files of the series as the planner keeps them (plan_assets), read against the rows of the sources."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = conn.execute("SELECT data FROM plan_assets WHERE series_id=? ORDER BY name", (series_id,)).fetchall()
    conn.close()
    return list(plan_inputs.assets_against([container_of(json.loads(data)) for (data,) in rows], online))


def namer_of(series: dict):
    """The series' own rule of names (modules/rules/formatter), as the container of an episode is named: an .mkv; a
    switched-off one hidden with `.off`."""
    from modules.rules import formatter

    def name(slot, video, off: bool) -> str:
        snapshot = video.snapshot
        item = {"season": slot[0], "episode_start": slot[1], "episode_end": None, "part": None,
                "resolution": snapshot.height, "voiceover_tag": snapshot.group, "source_url": snapshot.link}
        if video.kind == "cut" or (video.container is not None and video.container.video.cut_of is not None):
            item["cut"] = json.dumps({"height": snapshot.height, "voice_group": snapshot.group})
        made = formatter.format_name(series, formatter.build_metadata(series, item))
        return os.path.splitext(made)[0] + ".mkv" + (".off" if off else "")
    return name


async def inputs_of(real: RealSeries, series_id: int, rules: dict[int, list[dict]] | None = None
                    ) -> tuple[Inputs, Counter, dict]:
    """The tables of the planner for a real series, with `rules` in place of the stored rules of profiles: the layout the
    real constructor makes, answered as `composition.get` answers it, read by the planner's own function."""
    skipped: Counter = Counter()
    rows, info = await real.layout(series_id, rules)
    stored = replace(info["stored"], rows=rows)
    layout = layout_of(CompositionGetRequest(series_id=series_id), stored)
    voices = editor.view(info["voices"]).model_dump(mode="json")
    online, choices = plan_inputs.planner_inputs(layout, info["card"], voices)
    containers = asset_of(real.path, series_id, list(online))
    info["rows"] = rows
    return Inputs(online, tuple(containers), choices), skipped, info


def same_layout(rows: list[CompositionRow], stored: list[CompositionRow]) -> list[str]:
    """Where the layout of the real constructor here differs from the one the service wrote; empty when it is the same."""
    def key(row: CompositionRow) -> tuple:
        return (row.source_id, row.row_key, row.excluded, row.season, row.episode_start, row.episode_end, row.part,
                row.voice_group, row.voice_label, row.player)
    mine, theirs = {key(row) for row in rows}, {key(row) for row in stored}
    return [f"только здесь: {each}" for each in sorted(mine - theirs, key=repr)[:5]] + \
           [f"только у сервиса: {each}" for each in sorted(theirs - mine, key=repr)[:5]]


def run(coroutine):
    return asyncio.run(coroutine)
