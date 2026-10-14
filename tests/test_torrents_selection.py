"""Ядро выборочной загрузки: что качаем и какие приоритеты шлём в qBit."""
from modules.torrents import selection


def _row(**kw):
    row = {"original_path": "a.mkv", "renamed_path": None,
           "qb_file_index": None, "is_excluded": False}
    row.update(kw)
    return row


def _qb(name, index, priority=1):
    return {"name": name, "index": index, "priority": priority}


# --- is_wanted -------------------------------------------------------------

def test_file_without_episode_is_unrecognized():
    wanted, reason = selection.is_wanted({"season": 1}, set(), {})
    assert wanted is False
    assert reason == selection.REASON_UNRECOGNIZED


def test_ignored_season_excludes_file():
    wanted, reason = selection.is_wanted(
        {"season": 2, "episode": 5}, {2}, {})
    assert wanted is False
    assert reason == selection.REASON_USER


def test_ignored_episode_excludes_file():
    wanted, reason = selection.is_wanted(
        {"season": 1, "episode": 3}, set(), {1: {3}})
    assert (wanted, reason) == (False, selection.REASON_USER)


def test_episode_of_other_season_not_touched():
    wanted, reason = selection.is_wanted(
        {"season": 2, "episode": 3}, set(), {1: {3}})
    assert (wanted, reason) == (True, None)


def test_compilation_kept_while_any_episode_needed():
    meta = {"season": 1, "start": 1, "end": 3}
    assert selection.is_wanted(meta, set(), {1: {1, 2}}) == (True, None)
    assert selection.is_wanted(meta, set(), {1: {1, 2, 3}}) == (
        False, selection.REASON_USER)


def test_memory_parsed_from_json_shape():
    memory = selection.parse_memory({"4": [1, 2], "bad": [3], "5": []})
    assert memory == {4: {1, 2}}


# --- resolve ---------------------------------------------------------------

def test_index_wins_over_name():
    rows = [_row(original_path="old.mkv", qb_file_index=1, is_excluded=True)]
    result = selection.resolve(rows, [_qb("old.mkv", 0), _qb("new.mkv", 1)])
    assert result.to_disable == [0, 1]  # 0 не в реестре, 1 исключён


def test_name_fallback_for_legacy_rows():
    rows = [_row(original_path="a.mkv", renamed_path="Season 01/a.mkv")]
    result = selection.resolve(rows, [_qb("Season 01/a.mkv", 0, priority=0)])
    assert result.to_enable == [0]
    assert result.to_disable == []


def test_file_missing_in_db_is_disabled():
    result = selection.resolve([], [_qb("cover.jpg", 0)])
    assert result.to_disable == [0]


def test_no_calls_when_priorities_already_match():
    rows = [_row(qb_file_index=0), _row(original_path="b.mkv",
                                        qb_file_index=1, is_excluded=True)]
    result = selection.resolve(rows, [_qb("a.mkv", 0, priority=1),
                                      _qb("b.mkv", 1, priority=0)])
    assert result.to_enable == [] and result.to_disable == []


def test_wanted_empty_when_everything_excluded():
    rows = [_row(qb_file_index=0, is_excluded=True)]
    result = selection.resolve(rows, [_qb("a.mkv", 0, priority=0)])
    assert result.wanted_empty is True


def test_db_row_absent_in_qbit_reported_not_crash():
    rows = [_row(original_path="gone.mkv", qb_file_index=7)]
    result = selection.resolve(rows, [_qb("a.mkv", 0)])
    assert result.unmatched == ["gone.mkv"]


def test_index_missing_in_qbit_answer_falls_back_to_position():
    rows = [_row(original_path="a.mkv", qb_file_index=1, is_excluded=True)]
    result = selection.resolve(rows, [{"name": "x.mkv", "priority": 1},
                                      {"name": "a.mkv", "priority": 1}])
    assert result.to_disable == [0, 1]


def test_extra_file_is_wanted_regardless_of_episode():
    wanted, reason = selection.is_wanted({"is_extra": True}, set(), {})
    assert wanted is True
    assert reason is None

    # Дополнительные файлы (шрифты и т.д.) нужны даже при наличии исключений
    wanted, reason = selection.is_wanted(
        {"is_extra": True, "media_type": "extra"}, {1}, {1: {1, 2}})
    assert wanted is True
    assert reason is None

