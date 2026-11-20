"""The projection of a series: rows of all its sources with the numbers the constructor gave, voice-over groups and
unknown voice-overs, the summaries a window shows before any number exists."""


from core.contracts import (CompositionRow, Exclusion, ReleaseTable, RowState, RowVoice, RuleResult, RulesApplyReply, SeriesSource,
                            SourceType, TableRow)
from calc.layout.project import (SourceInput, lines_for, project)

def source(id=1, position=0, profile=7, **fields) -> SeriesSource:
    return SeriesSource(id=id, series_id=5, position=position, url=f"https://example.org/{id}",
                        source_type=SourceType.WEB_VIDEO, parser_profile_id=profile, **fields)


def row(key, text="", player="vk", voice="", kind="", group="", heights=(720,), state=RowState.PLAYABLE, label="",
        site_fields=None) -> TableRow:
    return TableRow(key=key, player=player, player_label=label, text=text, group=group,
                    voice=RowVoice(name=voice, kind=kind), heights=list(heights), state=state,
                    site_fields=site_fields or {})


def table(*rows, names=("Название",), site="ruchime") -> ReleaseTable:
    return ReleaseTable(url="https://example.org/page", site=site, names=list(names), read_at="2026-11-01T10:00:00Z",
                        complete=True, rows=list(rows))


def reply(results: dict) -> RulesApplyReply:
    return RulesApplyReply(results={key: RuleResult(**fields) for key, fields in results.items()})


def item(src: SeriesSource, tbl: ReleaseTable | None = None, results: dict | None = None,
         failure: str | None = None) -> SourceInput:
    return SourceInput(src, tbl, reply(results) if results is not None else None, failure)


def one(inp: SourceInput, groups=None, unknown=None) -> list[CompositionRow]:
    return project([inp], groups or {}, unknown or {}).rows


# ---- numbers: only from the constructor, rows kept as given ------------------------------------------------------


# ---- a source without a profile has no rows ----------------------------------------------------------------------


# ---- order -------------------------------------------------------------------------------------------------------


# ---- the choice of the source ------------------------------------------------------------------------------------


def catalog_table():
    return table(*[row(f"v{season}{number}", f"Серия {number}", player="vibix", group=f"Сезон {season}")
                   for season in (1, 2) for number in (1, 2)], row("k1", "1 серия", player="kodik", group="1 сезон"))


def test_a_switched_off_player_is_not_asked_about_and_is_in_the_layout_as_left_out_by_the_source():
    src = source(players_off=["kodik"])
    tbl = catalog_table()
    assert [line.key for line in lines_for(src, tbl)] == ["v11", "v12", "v21", "v22"]
    results = {key: {"episode": 1} for key in ("v11", "v12", "v21", "v22", "k1")}
    out = project([item(src, tbl, results)], {}, {})
    assert [(r.row_key, r.excluded, r.exclusion) for r in out.rows] == [
        ("v11", False, None), ("v12", False, None), ("v21", False, None), ("v22", False, None),
        ("k1", True, Exclusion.SOURCE)]
    [left] = [r for r in out.rows if r.excluded]
    assert left.left_out and left.decided and (left.season, left.episode_start, left.voice_label) == (None, None, "")
    # A row the source leaves out names no voice-over: it takes no number of an unknown one.
    assert (left.voice_group, left.voice_unknown) == (None, None)
    alone = project([item(source(), table(*[r for r in tbl.rows if r.player == "kodik"]), results)], {}, {})
    assert project([item(src, table(*[r for r in tbl.rows if r.player == "kodik"]), results)], {}, {}).unknown_voices == {}
    assert alone.unknown_voices != {}  # the same row in play does take one
    assert [p.player for p in out.sources[0].players] == ["vibix", "kodik"]


# ---- the lines the constructor is asked about --------------------------------------------------------------------


# ---- voice-over groups and unknown voice-overs -------------------------------------------------------------------


# ---- parts: copied from the answer, never made ---------------------------------------------------------------------


# ---- purity ------------------------------------------------------------------------------------------------------


# ---- the episodes of a layout and the episodes two layouts differ in --------------------------------------------------


# ---- the answers of the sites (synthetic, in the shape of the real ones: site_samples) -----------------------------------------------------------------------------------


# ---- the groups of voice-overs that changed ------------------------------------------------------------------------------


def labelled(*pairs) -> list[CompositionRow]:
    """Rows a, b, c… with the voice-over name and the label given."""
    return [CompositionRow(series_id=5, source_id=1, row_key=key, voice_name=name, voice_label=label, player="vk",
                           state=RowState.PLAYABLE) for key, name, label in pairs]


# ---- the order of the voice-overs of a season (stage 11) ------------------------------------------------------------
