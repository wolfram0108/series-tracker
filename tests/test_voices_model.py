"""The identity of a voice-over across the changes of the layout, explored over all small cases: which recorded row is a planned
voice-over (`adopt`), what becomes of the settings of groups that merge or take a name (`retarget`), and which variants a
chain may use (`plan`). The invariants hold for every case of the space, not for the cases someone thought of.

V1 every planned voice-over is recorded under one key, and no row is two voice-overs;
V2 a row that has its track is never set aside for a row of the same voice-over that has none;
V3 a row is chosen for a voice-over only when it is its own or shares a link with it;
V4 a switch-off survives a merge, equal picks are one, different picks go back to the level above, and the order in which the
   records are met does not matter;
V5 a chain holds no row of a variant that was left out, and what is left out is named by source and player."""
import itertools

import pytest

from core.contracts import EffectiveLever, EpisodeSettings, Level, Reference, ReferenceMark, group_target, variant_target
from modules.composition.settings import retarget
from modules.voices.planning import Candidate, PlannedDub, adopt, plan
from test_composition_settings import ALLOW, PLAYER, TAKE, rec
from test_voices_planning import NO_GROUPS, PRIORITY, dub_of, recorded, row

LINKS = ("https://site/a", "https://site/b")
STATUSES = ("pending", "downloading", "placed")
TRACKED = ("placed", "aligned")


def chains_of(links):
    return [tuple(each) for size in (1, 2) for each in itertools.permutations(links, size)]


def space_of_rows():
    """Up to two recorded rows of one episode, under the keys A and B, in each status, with each chain."""
    options = [(key, status, chain) for key in "AB" for status in STATUSES for chain in chains_of(LINKS)]
    yield []
    for one in options:
        yield [one]
    for one, two in itertools.combinations(options, 2):
        if one[0] != two[0]:
            yield [one, two]


def recorded_rows(spec):
    return [recorded(number, 1, key, *chain, status=status) for number, (key, status, chain) in enumerate(spec, start=1)]


@pytest.mark.parametrize("key", ["A", "B", "C"])
def test_a_planned_voice_over_gets_a_row_of_its_own_or_one_with_a_link_in_common_and_never_sets_a_track_aside(key):
    checked = 0
    for spec in space_of_rows():
        for chain in chains_of(LINKS):
            rows = recorded_rows(spec)
            chosen = adopt([dub_of(1, key, *chain)], rows)[(1, 1, 0, key)]
            candidates = [each for each in rows if each["voice_key"] == key
                          or {step["url"] for step in each["chain"]} & set(chain)]
            ready = [each for each in candidates if each["status"] in TRACKED]
            by_key = {each["voice_key"]: each for each in rows}
            if chosen in by_key:
                assert by_key[chosen] in candidates, (spec, chain, key, chosen)                          # V3
                if ready:
                    assert by_key[chosen]["status"] in TRACKED, (spec, chain, key, chosen)                # V2
            else:
                assert chosen == key and not ready, (spec, chain, key, chosen)                           # V1: its own, new
            checked += 1
    assert checked > 500


def test_two_planned_voice_overs_never_share_a_row_and_each_keeps_a_track_it_has():
    checked = 0
    for spec in space_of_rows():
        rows = recorded_rows(spec)
        for first, second in itertools.permutations(["A", "B", "C"], 2):
            for chain_one, chain_two in itertools.product(chains_of(LINKS), repeat=2):
                keys = adopt([dub_of(1, first, *chain_one), dub_of(1, second, *chain_two)], rows)
                assigned = list(keys.values())
                assert len(set(assigned)) == len(assigned), (spec, first, second, chain_one, chain_two, keys)     # V1
                by_key = {each["voice_key"]: each for each in rows}
                for dub_key, chain in ((first, chain_one), (second, chain_two)):
                    chosen = keys[(1, 1, 0, dub_key)]
                    ready = [each for each in rows if each["status"] in TRACKED and (
                        each["voice_key"] == dub_key or {step["url"] for step in each["chain"]} & set(chain))]
                    taken_by_other = {keys[(1, 1, 0, other)] for other in (first, second) if other != dub_key}
                    free_ready = [each for each in ready if each["voice_key"] not in taken_by_other]
                    if free_ready and chosen in by_key:
                        assert by_key[chosen]["status"] in TRACKED, (spec, first, second, chain_one, chain_two, keys)   # V2
                checked += 1
    assert checked > 5000


# --- the settings of groups that merge or take a name ---------------------------------------------------------------------

def settings_space():
    """For each of the groups A, B, C, no record, a switch-off, a switch-on, or a pick (one of two), at the series level."""
    values = [None, ("take", False), ("take", True), ("pick", 1), ("pick", 2)]
    for choice in itertools.product(values, repeat=3):
        yield dict(zip("ABC", choice))


def records_of(choice):
    found = []
    for group, value in choice.items():
        if value is None:
            continue
        kind, what = value
        if kind == "take":
            found.append(rec(TAKE, what, target=group_target(group)))
        else:
            found.append(rec(PLAYER, {"source_id": what, "player": "vk"}, target=group_target(group)))
    return found


RENAMES = ({"A": "C"}, {"A": "B"}, {"B": "A"}, {"A": "C", "B": "C"}, {"A": "B", "C": "B"}, {"A": "C", "B": "C", "C": "C"})


def test_the_settings_of_groups_that_merge_or_take_a_name_follow_one_rule_whatever_the_order():
    checked = 0
    for choice in settings_space():
        records = records_of(choice)
        for renames in RENAMES:
            told = []
            after = retarget(records, renames, told.append)
            reversed_after = retarget(list(reversed(records)), renames)
            assert sorted((each.lever, each.target, repr(each.value)) for each in after) == sorted(
                (each.lever, each.target, repr(each.value)) for each in reversed_after), (choice, renames)           # V4 order
            for lever in (TAKE, PLAYER):
                slots = [each for each in after if each.lever == lever]
                assert len({each.target for each in slots}) == len(slots), (choice, renames, lever)                   # one per name
            names = set(renames) | set(renames.values())
            for group, value in choice.items():
                if value is not None and group not in names:
                    assert any(each.target == group_target(group) for each in after), (choice, renames, group)    # untouched
            for new in set(renames.values()):
                joined = [choice.get(group) for group in set(renames) | {new} if renames.get(group, group) == new]
                joined = [value for value in joined if value is not None]
                takes = [value[1] for value in joined if value[0] == "take"]
                got = next((each for each in after if each.lever == TAKE and each.target == group_target(new)), None)
                if takes:
                    assert got is not None and got.value is (False if False in takes else True), (choice, renames, new)  # off wins
                picks = {value[1] for value in joined if value[0] == "pick"}
                got = next((each for each in after if each.lever == PLAYER and each.target == group_target(new)), None)
                if len(picks) == 1:
                    assert got is not None and got.value["source_id"] in picks, (choice, renames, new)
                if len(picks) > 1:
                    assert got is None and told, (choice, renames, new)                                               # back up
            checked += 1
    assert checked == 125 * len(RENAMES)


# --- the variants a chain may use ------------------------------------------------------------------------------------------

def test_a_chain_holds_no_variant_that_was_left_out_and_what_is_left_out_is_named_by_source_and_player():
    variants = [(1, "cvh"), (1, "kodik"), (2, "cvh"), (2, "kodik")]
    checked = 0
    for shared in (False, True):  # whether two variants show one link
        for left in itertools.chain.from_iterable(itertools.combinations(variants, size) for size in range(len(variants) + 1)):
            rows = [row(1, "cvh", "Ref", 1)]
            for source_id, player in variants:
                link = "https://site/same" if shared and (source_id, player) in ((1, "cvh"), (2, "cvh")) else None
                rows.append(row(source_id, player, "G", 1, link=link))
            references = [Reference(season=1, episode=1, mark=ReferenceMark.BY_LEVEL, source_id=1, row_key=rows[0].row_key)]
            levers = [EffectiveLever(lever="variant.allow", target=variant_target("G", source_id, player), value=False, level=Level.EPISODE)
                      for source_id, player in left]
            found = plan(rows, references, [EpisodeSettings(season=1, episode=1, levers=levers)], PRIORITY, NO_GROUPS)
            [dub] = [each for each in found.dubs if each.voice_key == "G"]
            offered = {(each.source_id, each.player) for each in dub.chain}
            links = {each.url for each in dub.chain}
            for address in left:
                denied_rows = [each for each in rows if (each.source_id, each.player) == address and each.voice_label == "G"]
                for denied in denied_rows:
                    if denied.link in links:
                        continue  # another variant that is not left out shows the same link: the link stays offered
                    assert address not in offered, (left, shared, dub.chain)                                          # V5
                    assert address in dub.left_out, (left, shared, dub.left_out)
            checked += 1
    assert checked > 20


# --- the voice-overs of an episode under every change of the plan, at every step of the work -------------------------------
#
# The real voices module over the neighbours of tests/test_voices.py, with a fake alignment service. The invariants after the
# work is quiet:
#   V6 the voice-overs recorded are the ones the plan has (no row of a voice that was taken off, none twice, none of an episode
#      that has no reference left);
#   V7 a job the service was told to cancel is held by no row;
#   V8 nothing is left over: the cleanup journal is empty, no file of the temporary folder is held by no row, no track in the
#      library is held by no placed row, and the folder of the service is empty unless a job is under way.

import os

from core.voice_names import NO_GROUPS
from modules.voices import planning as voices_planning
from modules.voices.settings import DEFAULT_PRIORITY
from test_voices import (db_path, dubs, downloads, conform_dir, library_files, placed_set, quiet, settled,  # noqa: F401
                         statuses_until, two_voices_ready, until_true)
from test_voices import synced  # noqa: E402
from test_voices_plan_exit import layout_changed, leave_out, pass_over, take_off


async def stage_pending(dubs):
    probe, neighbours, voices, bus = dubs
    from test_voices import linked, reference_on_disk
    neighbours.sources.append(linked("kodik", "RedHeadSound", range(1, 4)))
    neighbours.plan[:] = [(1, 1)]
    reference_on_disk(neighbours)
    sub = bus.subscribe("voices.dub.changed")
    layout_changed(probe)
    await statuses_until(sub, ["pending"] * 2)
    await quiet(voices)


async def stage_downloading(dubs):
    await two_voices_ready(dubs, finished=False)
    await quiet(dubs[2])


async def stage_aligning(dubs):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    await quiet(voices)


async def stage_placed(dubs):
    await placed_set(dubs)


STAGES = {"pending": stage_pending, "downloading": stage_downloading, "aligning": stage_aligning, "placed": stage_placed}


async def op_take_off_red(dubs):
    take_off(dubs[1], "RedHeadSound")


async def op_take_off_black(dubs):
    take_off(dubs[1], "BlackCat")


async def op_deny_cvh(dubs):
    leave_out(dubs[1], "BlackCat", "cvh")


async def op_deny_cvh_after_its_link_changed(dubs):
    dubs[1].sources[2]["links"]["1"] = "https://site/moved#cvh-BlackCat-1"
    leave_out(dubs[1], "BlackCat", "cvh")


async def op_merge_red_into_black(dubs):
    red = dubs[1].sources[-1]
    red["studio"], red["player"] = "BlackCat", "alloha"


async def op_reference_gone(dubs):
    neighbours = dubs[1]
    neighbours.plan[:] = []
    for name in os.listdir(neighbours.library):
        os.remove(os.path.join(neighbours.library, name))


OPS = {"take_off_red": op_take_off_red, "take_off_black": op_take_off_black, "deny_cvh": op_deny_cvh,
       "deny_cvh_link_changed": op_deny_cvh_after_its_link_changed, "merge_red_into_black": op_merge_red_into_black,
       "reference_gone": op_reference_gone}


def planned_labels(neighbours):
    found = voices_planning.plan(neighbours.rows(), neighbours.references_list(), [
        EpisodeSettings(season=season, episode=episode, levers=levers) for (season, episode), levers in neighbours.levers.items()],
        DEFAULT_PRIORITY, NO_GROUPS)
    return sorted((dub.episode, dub.voice_label) for dub in found.dubs)


async def voice_violations(dubs) -> list[str]:
    probe, neighbours, voices, bus = dubs
    await quiet(voices)

    async def drained():
        return not await voices.repo.cleanup_list()

    try:
        await until_true(drained, "the cleanup journal did not drain", seconds=8.0)
    except AssertionError:
        pass
    rows = await voices.repo.for_series(7)
    found: list[str] = []
    recorded = sorted((row["episode"], row["voice_label"]) for row in rows)
    if recorded != planned_labels(neighbours):
        found.append(f"V6 the rows are {recorded}, the plan has {planned_labels(neighbours)}")
    held = {row["job_id"] for row in rows if row["job_id"]}
    if set(neighbours.conform_cancels) & held:
        found.append(f"V7 jobs {set(neighbours.conform_cancels) & held} were cancelled and a row holds them")
    if await voices.repo.cleanup_list():
        found.append(f"V8 the journal is not empty: {[each['path'] for each in await voices.repo.cleanup_list()]}")
    owned = {path for row in rows for path in (row["temp_path"], row["aligned_path"], row["output_path"], row["move_from"]) if path}
    for root in (downloads(neighbours),):
        for folder, _, names in os.walk(root):
            found += [f"V8 {os.path.join(folder, name)} is held by no row" for name in names if os.path.join(folder, name) not in owned]
    for name in library_files(neighbours):
        path = os.path.join(neighbours.library, name)
        if name.endswith(".flac") and path not in owned:
            found.append(f"V8 the track {name} is held by no placed row")
    if not any(row["status"] == "aligning" for row in rows):
        left = [os.path.join(folder, name) for folder, _, names in os.walk(conform_dir(neighbours)) for name in names]
        found += [f"V8 {each} lies in the folder of the service and no job is under way" for each in left]
    return found


@pytest.mark.parametrize("stage, op", [(s, o) for s in STAGES for o in OPS], ids=[f"{s}-{o}" for s in STAGES for o in OPS])
async def test_the_voice_overs_of_an_episode_keep_their_invariants_under_every_change(stage, op, dubs):
    await STAGES[stage](dubs)
    probe, neighbours, voices, bus = dubs
    await OPS[op](dubs)
    asks = neighbours.layout_asks + 1
    synced(probe)
    await pass_over(voices, neighbours, asks)
    assert await voice_violations(dubs) == []


# --- a job of the service is the work of a batch of rows --------------------------------------------------------------------

import sqlite3

from core.db import Database
from modules.voices.repository import VoicesRepository
from scan_support import migrated_db
from series_factory import insert_series


@pytest.fixture(scope="module")
def voices_template(tmp_path_factory):
    path = migrated_db(tmp_path_factory.mktemp("voices") / "template.db")
    with sqlite3.connect(path) as conn:
        insert_series(conn, id=7, name="s", name_en="s", site="dunhuatv", url="https://site/title.html", source_type="web_video",
                      parser_profile_id=1)
        conn.commit()
    return path


HOLDINGS = (("J1", None), ("J2", None), (None, "S1"), (None, None))  # (job, submission key) a row may hold


async def test_a_job_or_submission_goes_to_the_journal_with_the_last_row_that_holds_it_and_not_before(voices_template, tmp_path):
    import shutil
    checked = 0
    for held in itertools.product(HOLDINGS, repeat=3):
        for order in itertools.permutations(range(3)):
            path = str(tmp_path / f"case{checked}.db")
            shutil.copy(voices_template, path)
            with sqlite3.connect(path) as conn:
                for number, (job, submission) in enumerate(held):
                    conn.execute(
                        "INSERT INTO voice_dubs (id, series_id, season, episode, part, voice_key, voice_label, chain, status, "
                        "job_id, submit_key, out_dir, updated_at, admitted) VALUES (?, 7, 1, 1, 0, ?, ?, '[]', 'aligning', ?, ?, '/w/out', 'now', 1)",
                        (number + 1, f"v{number}", f"v{number}", job, submission))
                conn.commit()
            repo = VoicesRepository(Database(path))
            staying = set(range(3))
            for number in order:
                await repo.retire(number + 1)
                staying.discard(number)
                journal = {each["path"] for each in await repo.cleanup_list() if each["kind"] in ("job", "submission")}
                held_by_staying = {value for each in staying for value in held[each] if value}
                held_by_gone = {value for each in set(range(3)) - staying for value in held[each] if value}
                assert journal == held_by_gone - held_by_staying, (held, order, number, journal)
            checked += 1
    assert checked == len(HOLDINGS) ** 3 * 6
