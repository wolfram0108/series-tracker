"""Simulator of the torrent editor (stage 15, prototype): the file stage of a release as one table seen in three slices.

The table: a row is a file path; the columns decode it. Slice 1 «files and episodes» (video files: episode and season by
the numbering preset, the role of every video folder), slice 2 «audio», slice 3 «subtitles» (external files: grouped by
folder and by the suffix after a video's name, matched to the episode videos by season and number, never by name).
For every series release the simulator counts what an operator must do and what no group rule closes (holes).

Reads the raw output of tools/collectors/torrent_files.py, collects nothing. Writes into data/torrent_slices/ (outside
git: the paths are the user's archive): releases.csv (a row per release), СВОДКА.md, and with --show the three slices of
the named releases.

    python tools/analyzers/torrent_slices.py <raw .jsonl> [--show "<root>" ...]
"""
from __future__ import annotations

import csv
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "torrent_slices"

VIDEO = {"mkv", "mp4", "avi", "ts", "m2ts", "mov", "wmv", "webm", "mpg", "mpeg", "vob", "m4v", "flv", "ogm", "divx",
         "rmvb"}
AUDIO = {"mka", "ac3", "dts", "aac", "flac", "mp3", "eac3", "wav", "m4a", "ogg", "opus", "thd", "dtshd", "mlp", "wv",
         "ape"}
SUBS = {"ass", "ssa", "srt", "sup", "sub", "idx", "vtt", "smi"}
FONTS = {"ttf", "otf", "ttc"}
MB = 1024 * 1024
# A video below this share of the release's main episode size is no episode of it (previews, menus, creditless).
EPISODE_SHARE = 0.25
# Folder names that say what lies inside, not whose it is: such a folder does not name a voice-over.
GENERIC = re.compile(r"^(rus|eng|jpn|ru|en|int|ext)?[ ._-]*(sound|audio|sounds|audios|subs?|subtitles?|sub\.rus|"
                     r"fonts?|dub|dubs|озвучк[аи]|субтитры|звук)?[ ._-]*(rus|eng|ru|en)?$", re.I)

# The numbering preset: explicit episode markers only, in order. A leading number is no marker (films «25. …»).
EPISODE = [
    ("SxxEyy", re.compile(r"(?i)\bS(\d{1,2})[ ._-]?E(\d{1,4})(?!\d)")),
    ("of", re.compile(r"(?i)\[?(\d{1,4})[ _]of[ _]\d{1,4}")),
    ("Ep", re.compile(r"(?i)(?:^|[^a-z])(?:ep|e)[ ._]?(\d{1,4})(?!\d)")),
    ("dash", re.compile(r"(?:\s|_)-(?:\s|_)(\d{1,4})(?:v\d)?(?=\D|$)")),
    ("brackets", re.compile(r"\[(\d{1,4})(?:v\d)?\]")),
    ("серия", re.compile(r"(?i)(?:серия|серии|episode)[ ._]*(\d{1,4})|(\d{1,4})[ ._]*(?:серия|episode)")),
]
SEASON = re.compile(r"(?i)\bS(\d{1,2})(?![\d])(?!E\d)|season[ ._]*(\d{1,2})|сезон[ ._]*(\d{1,2})|\(s(\d{1,2})\)|"
                    r"\bTV[- ]?(\d)\b|(\d)(?:st|nd|rd|th)[ ._]season|(\d{1,2})[ ._]сезон")


def kind_of(path: str) -> str:
    ext = PurePosixPath(path).suffix.lower().lstrip(".")
    if ext in VIDEO:
        return "видео"
    if ext in AUDIO:
        return "звук"
    if ext in SUBS:
        return "субтитры"
    if ext in FONTS:
        return "шрифт"
    return "прочее"


CROSS = re.compile(r"(?i)(?<![\dx])(\d{1,2})x(\d{1,3})(?![\dp])")
# Numbers of a name that are no episode: resolutions, codecs, channels.
TECHNICAL = {"480", "576", "720", "1080", "2160", "264", "265", "10", "8"}
BRACKETED = re.compile(r"\[[^\]]*\]|\((?!\d{1,4}\))[^)]*\)")


def lone_number(name: str) -> int | None:
    """The last number standing alone in a name, after the bracketed technical tags are dropped: `One Punch Man 02
    [BDRip …]`, `Gintama-028`, `(19)`, `96`, `62. Title`."""
    # Channel layouts (AAC2.0, DDP5.1), codecs (H.264, x265) and resolutions (1080p) carry digits of no episode.
    plain = re.sub(r"(?i)\d\.\d(?!\d)|[hx]\.?26[45]|\b\d{3,4}[pi]\b|\b\d{1,2}bit\b", " ", BRACKETED.sub(" ", name))
    tokens = [token for token in re.split(r"[\s_.\-()]+", plain) if token.isdigit()]
    tokens = [token for token in tokens if token not in TECHNICAL and not 1900 <= int(token) <= 2100]
    return int(tokens[-1]) if tokens else None


def episode_of(name: str) -> tuple[int | None, int | None]:
    """(season, episode) of a file name by the preset; a number that reads as a year is no episode."""
    for label, pattern in EPISODE:
        found = pattern.search(name)
        if not found:
            continue
        if label == "SxxEyy":
            return int(found.group(1)), int(found.group(2))
        number = int(next(group for group in found.groups() if group))
        if 1900 <= number <= 2100:
            continue
        return None, number
    cross = CROSS.search(name)
    if cross:
        return int(cross.group(1)), int(cross.group(2))
    return None, lone_number(name)


def season_marker(text: str) -> int | None:
    found = SEASON.search(text)
    return int(next(group for group in found.groups() if group)) if found else None


def season_of_dirs(dirs: list[str], root: str) -> int | None:
    """The nearest folder (or the release root) that names a season."""
    for name in reversed(dirs):
        season = season_marker(name)
        if season is not None:
            return season
    return season_marker(root or "")


class Release:
    def __init__(self, record: dict) -> None:
        self.torrent, self.root = record["torrent"], record["root"] or ""
        self.files = [(each["path"], each["size"]) for each in record["files"]]

    @property
    def key(self) -> str:
        return self.root or (self.files[0][0] if self.files else self.torrent)

    @property
    def disc(self) -> bool:
        return any(re.search(r"(?i)(^|/)(VIDEO_TS|BDMV)(/|$)|\.(iso|vob)$", path) for path, _ in self.files)

    def big_videos(self) -> int:
        return sum(1 for path, size in self.files if kind_of(path) == "видео" and size >= 50 * MB)


def stem(path: str) -> str:
    return PurePosixPath(path).stem


def simulate(release: Release) -> dict:
    rows = [{"path": path, "size": size, "kind": kind_of(path), "dir": str(PurePosixPath(path).parent)}
            for path, size in release.files]
    for row in rows:
        row["dirs"] = [] if row["dir"] == "." else row["dir"].split("/")
        # The season a file names itself (SxxEyy) and the one its folders name are kept apart: a folder's season is
        # the operator's to correct, the file's own is not.
        row["fseason"], row["episode"] = episode_of(stem(row["path"]))
        row["dseason"] = season_of_dirs(row["dirs"], release.root)

    # Slice 1: video folders and their role.
    video_groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["kind"] == "видео":
            video_groups[row["dir"]].append(row)
    medians = {name: statistics.median(each["size"] for each in group) for name, group in video_groups.items()}
    top = max(medians.values(), default=0)
    series_dirs, other_dirs = [], []
    for name, group in video_groups.items():
        numbered = sum(1 for each in group if each["episode"] is not None)
        big = medians[name] >= EPISODE_SHARE * top
        (series_dirs if big and (numbered * 2 >= len(group) or len(group) >= 3) else other_dirs).append(name)
    actions = Counter()
    holes = Counter()
    notes: list[str] = []
    # Every video folder beside the main one needs the operator's word on its role; a numbered one that is no
    # episode folder (previews, OVA) would otherwise claim the numbers of episodes.
    main = max(series_dirs, key=lambda name: len(video_groups[name]), default=None)
    for name in video_groups:
        if name != main and (name in series_dirs or any(each["episode"] is not None for each in video_groups[name])):
            actions["роль видео-набора"] += 1
    # A folder without a season marker is season 1, as the series card would take it; clashes show where not.
    folder_season = {name: (video_groups[name][0]["dseason"] or 1) for name in series_dirs}

    def video_key(row: dict, name: str) -> tuple:
        return (row["fseason"] or folder_season[name], row["episode"])

    keys: dict[tuple, list[str]] = defaultdict(list)
    for name in series_dirs:
        group = video_groups[name]
        unnumbered = [each for each in group if each["episode"] is None]
        if unnumbered and len(unnumbered) == len(group):
            actions["нумерация набора по порядку"] += 1
            for index, each in enumerate(sorted(group, key=lambda row: row["path"]), start=1):
                each["episode"] = index
        else:
            holes["видео без номера"] += len(unnumbered)
        for each in group:
            if each["episode"] is not None:
                keys[video_key(each, name)].append(name)
    clash_dirs = {name for owners in keys.values() if len(set(owners)) > 1 for name in owners}
    if clash_dirs:
        # Two folders give the same numbers: the operator sets each folder's season; the simulator then gives every
        # clashing folder a season of its own, as the operator would.
        actions["сезон видео-набора"] += len(clash_dirs)
        notes.append("одинаковые номера в " + ", ".join(sorted(clash_dirs)))
        for name in clash_dirs:
            folder_season[name] = f"[{name}]"
        keys = defaultdict(list)
        for name in series_dirs:
            for each in video_groups[name]:
                if each["episode"] is not None:
                    keys[video_key(each, name)].append(name)
    same_folder = sum(len(owners) - 1 for owners in keys.values() if len(set(owners)) == 1 and len(owners) > 1)
    holes["дубль номера в одном наборе"] += same_folder
    seasons = {season for season, _ in keys}
    episodes = {episode for _, episode in keys}
    series_keys = set(keys)
    by_season = {season: name for name, season in folder_season.items()}

    def owner_dir(row: dict) -> str | None:
        """The episode folder an external file lies under (the deepest one); the root folder holds everything."""
        under = [name for name in series_dirs if name == "." or row["dir"] == name or row["dir"].startswith(name + "/")]
        return max(under, key=lambda name: 0 if name == "." else len(name), default=None)

    def season_of_external(row: dict):
        if row["fseason"] is not None:
            return folder_season.get(by_season.get(row["fseason"]), row["fseason"])
        owner = owner_dir(row)
        if owner is not None and owner != ".":
            return folder_season[owner]
        if row["dseason"] is not None and row["dseason"] in by_season:
            return folder_season[by_season[row["dseason"]]]
        if owner == ".":
            return folder_season["."]
        return None

    # Slices 2 and 3: external audio and subtitles, grouped by folder and by suffix after a video's name.
    video_stems = {stem(each["path"]) for name in series_dirs for each in video_groups[name]}
    ext_groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        if row["kind"] not in ("звук", "субтитры"):
            continue
        suffix = ""
        if row["dir"] in video_groups:
            name = stem(row["path"])
            base = max((each for each in video_stems if name.startswith(each)), key=len, default="")
            suffix = name[len(base):].strip(" ._-") if base else ""
        ext_groups[(row["kind"], row["dir"], suffix)].append(row)
    slice_rows = []
    named_parents: dict[str, set] = defaultdict(set)
    for (kind, folder, suffix), group in sorted(ext_groups.items()):
        own = PurePosixPath(folder).name if folder != "." else ""
        source = "суффикс" if suffix else ("каталог" if own and not GENERIC.match(own) and folder not in video_groups
                                           else "вручную")
        matched = orphan = unnumbered = ambiguous = 0
        covered = set()
        for row in group:
            if row["episode"] is None:
                unnumbered += 1
                continue
            season = season_of_external(row)
            if season is None:
                options = [key for key in series_keys if key[1] == row["episode"]]
                if len(options) == 1:
                    season = options[0][0]
                elif len(options) > 1:
                    ambiguous += 1
                    continue
            if (season, row["episode"]) in series_keys:
                matched += 1
                covered.add((season, row["episode"]))
            else:
                orphan += 1
        holes[f"{kind}: без номера"] += unnumbered
        holes[f"{kind}: номер без видео"] += orphan
        holes[f"{kind}: сезон неоднозначен"] += ambiguous
        if source == "вручную":
            actions[f"{kind}: название вручную"] += 1
        else:
            named_parents[kind].add((str(PurePosixPath(folder).parent), source))
        slice_rows.append({"kind": kind, "folder": folder, "suffix": suffix, "files": len(group), "source": source,
                           "matched": matched, "covered": len(covered), "orphan": orphan, "unnumbered": unnumbered,
                           "ambiguous": ambiguous})
    for kind, parents in named_parents.items():
        # Sibling folders named by the same part of the path take one decision: «name from this folder level».
        actions[f"{kind}: название из пути"] += len(parents)
    fonts = sum(1 for row in rows if row["kind"] == "шрифт")
    other = Counter(row["kind"] for row in rows if row["kind"] == "прочее")["прочее"]
    return {"release": release.key, "torrent": release.torrent, "rows": len(rows),
            "video_dirs": len(video_groups), "series_dirs": len(series_dirs), "episodes": len(series_keys),
            "seasons": len(seasons), "audio_groups": sum(1 for each in slice_rows if each["kind"] == "звук"),
            "sub_groups": sum(1 for each in slice_rows if each["kind"] == "субтитры"), "fonts": fonts, "other": other,
            "actions": dict(actions), "holes": {key: value for key, value in holes.items() if value},
            "notes": notes, "slices": {"video": {name: video_groups[name] for name in video_groups},
                                       "series_dirs": series_dirs, "ext": slice_rows}, "max_episode": max(episodes, default=0)}


def show(result: dict) -> str:
    lines = [f"## {result['release']}", "", f"Строк {result['rows']}, серий {result['episodes']}, сезонов "
             f"{result['seasons']}. Действия: {result['actions'] or 'нет'}. Дыры: {result['holes'] or 'нет'}.", "",
             "### Срез 1 — файлы и серии", "", "| Видео-набор | Файлов | С номером | Роль (по умолчанию) |",
             "|---|---|---|---|"]
    for name, group in result["slices"]["video"].items():
        numbered = sum(1 for each in group if each["episode"] is not None)
        role = "серии" if name in result["slices"]["series_dirs"] else "не серии — решает оператор"
        lines.append(f"| `{name}` | {len(group)} | {numbered} | {role} |")
    for kind, title in (("звук", "2 — звук"), ("субтитры", "3 — субтитры")):
        lines += ["", f"### Срез {title}", "", "| Набор | Суффикс | Файлов | Название из | Легло на серии | Без номера | "
                  "Номер без видео |", "|---|---|---|---|---|---|---|"]
        for each in result["slices"]["ext"]:
            if each["kind"] == kind:
                lines.append(f"| `{each['folder']}` | {each['suffix'] or '—'} | {each['files']} | {each['source']} | "
                             f"{each['covered']} | {each['unnumbered']} | {each['orphan']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    raw = Path(sys.argv[1])
    wanted = [sys.argv[i + 1] for i, arg in enumerate(sys.argv) if arg == "--show"]
    releases: dict[str, Release] = {}
    for line in raw.open(encoding="utf-8"):
        record = json.loads(line)
        if record["error"] or not record["files"]:
            continue
        release = Release(record)
        held = releases.get(release.key)
        if held is None or len(release.files) > len(held.files):
            releases[release.key] = release
    OUT.mkdir(parents=True, exist_ok=True)
    results, discs = [], 0
    for release in releases.values():
        if release.disc:
            discs += 1
            continue
        if release.big_videos() < 3:
            continue
        results.append(simulate(release))
    with (OUT / "releases.csv").open("w", encoding="utf-8", newline="") as sink:
        writer = csv.writer(sink)
        writer.writerow(["раздача", "торрент", "строк", "видео-наборов", "наборов серий", "серий", "сезонов",
                         "наборов звука", "наборов субтитров", "шрифтов", "прочих", "действий", "дыр", "действия",
                         "дыры", "заметки"])
        for each in results:
            writer.writerow([each["release"], each["torrent"], each["rows"], each["video_dirs"], each["series_dirs"],
                             each["episodes"], each["seasons"], each["audio_groups"], each["sub_groups"], each["fonts"],
                             each["other"], sum(each["actions"].values()), sum(each["holes"].values()),
                             json.dumps(each["actions"], ensure_ascii=False), json.dumps(each["holes"], ensure_ascii=False),
                             "; ".join(each["notes"])])
    summary = summarize(results, discs, len(releases))
    (OUT / "СВОДКА.md").write_text(summary, encoding="utf-8")
    if wanted:
        picked = [each for each in results if any(each["release"].startswith(name) for name in wanted)]
        (OUT / "СРЕЗЫ.md").write_text("\n".join(show(each) for each in picked), encoding="utf-8")
        print(f"SHOWN {len(picked)}")
    print(summary.split("\n## ")[0])
    print("TORRENT_SLICES_DONE")


def summarize(results: list[dict], discs: int, total: int) -> str:
    def band(value: int) -> str:
        return "0" if value == 0 else "1–2" if value <= 2 else "3–5" if value <= 5 else "6–10" if value <= 10 else "> 10"

    lines = [f"# Симулятор срезов: файловая стадия", "",
             f"Раздач в архиве {total}; дисковых (вне редактора) {discs}; сериальных (три видео от 50 МБ и больше) "
             f"{len(results)}.", ""]
    with_holes = [each for each in results if each["holes"]]
    lines.append(f"Без дыр: {len(results) - len(with_holes)}; с дырами (строки, которые не закрывает ни одна группа): "
                 f"{len(with_holes)}.")
    lines += ["", "## Действий оператора на раздачу", "", "| Действий | Раздач |", "|---|---|"]
    bands = Counter(band(sum(each["actions"].values())) for each in results)
    for name in ("0", "1–2", "3–5", "6–10", "> 10"):
        lines.append(f"| {name} | {bands[name]} |")
    lines += ["", "## Виды действий", "", "| Действие | Раздач | Всего |", "|---|---|---|"]
    kinds = defaultdict(lambda: [0, 0])
    for each in results:
        for name, value in each["actions"].items():
            kinds[name][0] += 1
            kinds[name][1] += value
    for name, (count, value) in sorted(kinds.items(), key=lambda item: -item[1][0]):
        lines.append(f"| {name} | {count} | {value} |")
    lines += ["", "## Виды дыр", "", "| Дыра | Раздач | Строк |", "|---|---|---|"]
    holes = defaultdict(lambda: [0, 0])
    for each in results:
        for name, value in each["holes"].items():
            holes[name][0] += 1
            holes[name][1] += value
    for name, (count, value) in sorted(holes.items(), key=lambda item: -item[1][0]):
        lines.append(f"| {name} | {count} | {value} |")
    lines += ["", "## Раздачи с дырами (по числу строк)", "", "| Раздача | Действий | Дыры | Заметки |",
              "|---|---|---|---|"]
    for each in sorted(with_holes, key=lambda item: -sum(item["holes"].values()))[:60]:
        lines.append(f"| `{each['release']}` | {sum(each['actions'].values())} | "
                     f"{json.dumps(each['holes'], ensure_ascii=False)} | {'; '.join(each['notes'])} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
