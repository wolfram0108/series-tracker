"""A real series as the tables of the planner (core/plan), read from a copy of the service's database.

The rows come out of the real constructor: the stored tables of the pages go through the rules of their profiles
(modules/rules: compile_profile, process_title, result_of, settle_parts) and the layout (modules/composition/project),
with the groups of voice-overs of the settings — the same functions the service runs. Other rules can be given in place of
the stored ones: a situation «this rule is changed» is the real constructor run with them. The asset (files and tracks)
comes from media_items and voice_dubs; the choices from the settings and the choice of the reference.

Nothing is written: the database is copied first (sqlite backup of a read-only connection), and every repository reads
the copy."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from core.contracts import CompositionRow, RulesApplyReply, SeriesSource, SourceType  # noqa: E402
from core.db import Database  # noqa: E402
from core.plan import inputs as plan_inputs  # noqa: E402
from core.plan.tables import Choices, Container, Inputs, OnlineRow, RowId, Snapshot, Track, Video  # noqa: E402
from modules.catalog.repository import CatalogRepository  # noqa: E402
from modules.composition import project  # noqa: E402
from modules.composition.repository import CompositionRepository  # noqa: E402
from modules.rules.engine import compile_profile, process_title  # noqa: E402
from modules.rules.module import result_of  # noqa: E402
from modules.rules.numbering import settle_parts  # noqa: E402
from modules.rules.repository import RulesRepository  # noqa: E402
from modules.settings.repository import SettingsRepository  # noqa: E402
from modules.sources.players.alloha import AllohaPlayer  # noqa: E402
from modules.sources.players.allvideo import AllVideoPlayer  # noqa: E402
from modules.sources.players.animakima import AnimakimaPlayer  # noqa: E402
from modules.sources.players.cdnvideohub import CdnVideoHubPlayer  # noqa: E402
from modules.sources.players.kodik import KodikPlayer  # noqa: E402
from modules.sources.players.vibix import VibixPlayer  # noqa: E402
from modules.sources.players.vk import VkPlayer  # noqa: E402
from modules.sources.repository import SourcesRepository  # noqa: E402
from modules.voices.settings import PREFIX, parse  # noqa: E402

PROD_DB = os.path.join(ROOT, "app.db")


def copy_db(target: str) -> str:
    """A copy of the service's database, made through a read-only connection."""
    os.makedirs(os.path.dirname(target), exist_ok=True)
    source = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    copy = sqlite3.connect(target)
    source.backup(copy)
    copy.close()
    source.close()
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


def online_of(rows: list[CompositionRow], places: dict[int, str], skipped: Counter) -> list[OnlineRow]:
    return list(plan_inputs.online_of(rows, places))


PLAYERS = (VkPlayer, AllVideoPlayer, CdnVideoHubPlayer, KodikPlayer, AnimakimaPlayer, AllohaPlayer, VibixPlayer)


def number_of(player: str | None, link: str) -> str:
    """The video a link names, as its host numbers it (the players' own `video_of`); '' when the link names none."""
    if not link:
        return ""
    named = [each for each in PLAYERS if each.name == player]
    for each in named or PLAYERS:
        found = each.video_of(link)
        if found:
            return found
    return ""


def asset_of(path: str, series_id: int, online: list[OnlineRow], places: dict[int, str], order: list[str],
             skipped: Counter) -> list[Container]:
    """The files of the episodes and their tracks, as the database of the service records them."""
    by_key = {(row.id.source_id, row.id.row_key): row for row in online}
    by_link = {row.link: row for row in online if row.link}

    def row_id(source_id, row_key, link: str = "", player: str | None = None) -> RowId:
        """The row of a file: the online row while it names the same video; another video behind it (both numbers
        known and different) is another row, offline (В-6)."""
        found = by_key.get((source_id, row_key)) if row_key else by_link.get(link)
        number = number_of(player or (found.player if found is not None else None), link)
        if found is not None and not (number and found.id.video and number != found.id.video):
            return found.id
        return RowId(source_id if source_id is not None else -1, row_key or link, number)

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    items = conn.execute("SELECT * FROM media_items WHERE series_id=? AND status='completed' AND final_filename IS NOT NULL "
                         "AND plan_status='in_plan_single'", (series_id,)).fetchall()
    dubs = conn.execute("SELECT * FROM voice_dubs WHERE series_id=? AND status='placed'", (series_id,)).fetchall()
    conn.close()
    tracks_of: dict[tuple, list[Track]] = {}
    for dub in dubs:
        chain = json.loads(dub["chain"] or "[]")
        if not chain:
            skipped["озвучка без цепочки"] += 1
            continue
        candidate = chain[min(dub["attempt"], len(chain) - 1)]
        source_id = candidate.get("source_id")
        a_source, _, a_key = (dub["aligned_to"] or "").partition(":")
        aligned_to = row_id(int(a_source), a_key) if a_source.isdigit() else RowId(-1, dub["aligned_to"] or "", "")
        snapshot = Snapshot(places.get(source_id, ""), candidate.get("player", "?"), dub["voice_label"], 720,
                            candidate.get("url", ""))
        place = (dub["season"], dub["episode"]) + ((dub["part"],) if dub["part"] else ())
        tracks_of.setdefault(place, []).append(
            Track(row_id(source_id, candidate.get("row_key"), candidate.get("url", ""), candidate.get("player")),
                  snapshot, place, aligned_to))
    containers = []
    for item in items:
        slot = (item["season"], item["episode_start"]) + ((item["part"],) if item["part"] is not None else ())
        own = row_id(item["source_id"], item["row_key"], item["source_url"] or "")
        found = by_key.get((item["source_id"], item["row_key"]))
        snapshot = Snapshot(places.get(item["source_id"], ""), found.player if found is not None else "?",
                            item["voiceover_tag"], item["resolution"] or 0, item["source_url"] or "")
        cut = json.loads(item["cut"]) if item["cut"] else None
        if cut:
            match = next((row for row in online if row.compilation and row.id.source_id == cut["source_id"]
                          and (row.start, row.end) == (cut.get("start"), cut.get("end"))), None)
            compilation = match.id if match is not None else RowId(cut["source_id"], cut.get("key", ""), "")
            video = Video(compilation, Snapshot(places.get(cut["source_id"], ""), cut.get("player", "?"),
                                                cut.get("voice_group"), cut.get("height") or 0), slot, own, cut_of=compilation)
        else:
            video = Video(own, snapshot, slot, own)
        # A track is aligned to the video of its file: to the video the file holds, whatever is behind its row now.
        held = [Track(each.dub, each.snapshot, each.episode, own) if (each.aligned_to.source_id, each.aligned_to.row_key)
                == (own.source_id, own.row_key) else each for each in tracks_of.get(slot, ())]
        mine = tuple(sorted(held, key=lambda track: (
            order.index(track.group) if track.group in order else len(order), track.group or "")))
        containers.append(Container(item["final_filename"], video, mine, bool(item["is_ignored_by_user"])))
    return containers


def choices_of(path: str, series_id: int, info: dict, online: list[OnlineRow]) -> Choices:
    """What the user chose: the orders of the settings, and the levers of every episode as the service's own function
    (modules/composition/settings.effective) resolves them through its levels, with the compilation it stands in — read by
    the planner's own mapping (core/plan/inputs.py)."""
    from modules.composition import compilations as membership
    from modules.composition import settings as levers

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    off = {(season, episode) for season, episode in conn.execute(
        "SELECT season, episode FROM scan_references WHERE series_id=? AND mark='off' AND part IS NULL", (series_id,))}
    conn.close()
    stored = info["stored"]
    order = [label for season in sorted(stored.orders) for label in stored.orders[season]]
    records = stored.records
    rows = info["rows"]
    source_order = {each.source_id: place for place, each in enumerate(stored.sources)}
    members = membership.resolve(rows, records, source_order).members
    episodes = []
    for slot in sorted({row.slot[:2] for row in online if not row.compilation}):
        stands = members.get(slot)
        episodes.append((slot[0], slot[1], levers.effective(records, slot[0], slot[1],
                                                             stands.key if stands is not None else None)))
    by_key = plan_inputs.compilations_of(rows, online)
    compilation_levers = [(key, levers.effective(records, row_id_season(online, row_id), None, key))
                          for key, row_id in by_key.items()]
    return plan_inputs.choices_of(rows, online, episodes, compilation_levers, players=info["voices"].priority,
                                  player_groups=info["voices"].players, sources=[each.source_id for each in stored.sources],
                                  voice_order=order, off_episodes=off)


def row_id_season(online: list[OnlineRow], row_id: RowId) -> int:
    return next(each.season for each in online if each.id == row_id)


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
    """The tables of the planner for a real series, with `rules` in place of the stored rules of profiles."""
    skipped: Counter = Counter()
    rows, info = await real.layout(series_id, rules)
    online = online_of(rows, info["places"], skipped)
    order = list(info["stored"].orders and [l for s in sorted(info["stored"].orders) for l in info["stored"].orders[s]])
    containers = asset_of(real.path, series_id, online, info["places"], order, skipped)
    info["rows"] = rows
    return Inputs(tuple(online), tuple(containers), choices_of(real.path, series_id, info, online)), skipped, info


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


def agreement(path: str, series_id: int, inputs: Inputs, made) -> tuple[Counter, Counter]:
    """The planner's choices against the service's: the reference of every episode (scan_references) and the source of
    every voice-over the service planned (voice_dubs: the candidate its attempts came to for a placed track, the first
    of its chain for one to come)."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    service = {(r["season"], r["episode"]): (r["source_id"], r["row_key"]) for r in conn.execute(
        "SELECT season, episode, source_id, row_key FROM scan_references WHERE series_id=? AND part IS NULL", (series_id,))}
    links = {row.link: row for row in inputs.online}
    planned = {}
    for r in conn.execute("SELECT season, episode, voice_label, chain, attempt, status FROM voice_dubs WHERE series_id=? "
                          "AND part=0", (series_id,)):
        chain = json.loads(r["chain"] or "[]")
        if chain:
            used = chain[min(r["attempt"], len(chain) - 1)] if r["status"] == "placed" else chain[0]
            found = links.get(used.get("url"))
            planned[((r["season"], r["episode"]), r["voice_label"])] = (found.id.source_id, found.id.row_key) if found else None
    conn.close()
    references, voices = Counter(), Counter()
    for goal in made.goals:
        if len(goal.slot) != 2:
            continue
        mine = goal.video.timeline if goal.video.kind != "cut" else goal.video.ref2.timeline
        theirs = service.get(goal.slot)
        references["тот же" if theirs and (mine.source_id, mine.row_key) == theirs else "другой"] += 1
        for way in goal.tracks:
            theirs = planned.get((goal.slot, way.voice))
            voices["у сервиса нет" if theirs is None else "тот же" if theirs == (way.dub.source_id, way.dub.row_key)
                   else "другой"] += 1
    return references, voices
