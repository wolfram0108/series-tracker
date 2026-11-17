// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest"
import { mount, type VueWrapper } from "@vue/test-utils"
import ParserTableCard from "./ParserTableCard.vue"
import type { EditableRule } from "./ParserRuleCard.vue"
import { emptyRow, rowOf, storedOf } from "../../lib/ruleTable"
import { t } from "../../i18n"

const LINES = [
  "PID:1.ruchime.org | P:Kodik | Вечная воля | Серия 1 | V:AniStar | VT:voice | R:1080",
  "PID:1.ruchime.org | P:CVH | Вечная воля | Серия 1 | V:AnimeVost | VT:voice | R:1080",
  "PID:dunhuatv.ru | P:Kodik | Вечная воля | 1 серия | V:ChinaTown | VT:voice | R:720",
]
const CODES = ["PID", "P", "G", "V", "VT", "R"]

let mounted: VueWrapper | null = null
afterEach(() => mounted?.unmount())

function opened(rows = [rowOf("V", "AniStar")]) {
  const rule: EditableRule = { id: 5, name: "Озвучки", continue_after_match: true, conditions: [], actions: [],
                               table: { action_type: "assign_voiceover", rows } }
  mounted = mount(ParserTableCard, { props: { rule, index: 0, total: 1, isOpen: true, lines: LINES, codes: CODES } })
  return { w: mounted, rule }
}
const chip = (w: VueWrapper, code: string) => w.findAll(".template-chip").find((each) => each.text().startsWith(`${code}:`))!

describe("a rule «Таблица»", () => {
  it("is one table: a header «ЕСЛИ» with a template for each field of the line, «ТО» with the action of the column", () => {
    const { w } = opened()
    expect(w.findAll("table.rule-table")).toHaveLength(1)
    expect(w.findAll(".template-chip").map((each) => each.text())).toEqual(["PID:2", "P:2", "G:0", "V:2", "VT:1", "R:2"])
    expect(chip(w, "G").attributes("disabled")).toBeDefined()
    // The actions of «ТО» are numbered: the head shows the number of the column's action as its mark, a row shows its by number.
    expect(w.find("th.col-then .number-mark").text()).toBe("5")
    expect(w.find("th.col-then .table-action .selected-value").text()).toBe(t("rules.action.assign_voiceover"))
    expect(w.find(".rule-rows").text()).toBe(t("rules.table.rows", { count: 1 }))
  })

  it("gives a row of a folder of subtitles its name, language and type (П/Н), and a row its own action by its number", async () => {
    const row = rowOf("T", "Signs")
    const rule: EditableRule = { id: 5, name: "Субтитры", continue_after_match: true, conditions: [], actions: [],
                                 table: { action_type: "assign_subtitle_folder", rows: [row] } }
    mounted = mount(ParserTableCard, { props: { rule, index: 0, total: 1, isOpen: true, lines: LINES, codes: CODES, place: "subtitles" } })
    const types = mounted.findAll("td.col-then .torrent-types button")
    expect(types.map((each) => each.text())).toEqual([t("torrent.type.short.full"), t("torrent.type.short.signs")])
    await types[1].trigger("click")
    expect(JSON.parse(rule.table!.rows[0].then.value)).toEqual({ name: "Signs", type: "signs" })
    // The row shows the number of the column's action while it has none of its own: «В каталог субтитров» is the first of «Субтитры».
    expect(mounted.find("td.col-then .row-action .selected-value").text()).toBe("1")
  })

  it("adds by a template a row for each value of the field the table does not hold, marked, value in «ТО»", async () => {
    const { w, rule } = opened()
    await chip(w, "V").trigger("click")
    expect(rule.table!.rows.map((row) => [row.if.blocks[0].value, row.then.value])).toEqual([
      ["V:AniStar", "AniStar"], ["V:AnimeVost", "AnimeVost"], ["V:ChinaTown", "ChinaTown"]])
    expect(w.findAll("tbody tr.is-fresh")).toHaveLength(2)
    expect(chip(w, "V").text()).toBe("V:0")
    expect(chip(w, "V").attributes("disabled")).toBeDefined()
  })

  it("keeps the number typed for a season, an episode or a part as the text the core stores, and the table is saved", async () => {
    for (const action of ["assign_season", "assign_episode", "assign_part"]) {
      const row = rowOf("PID", "dunhuatv.ru")
      row.then.value = ""
      const rule: EditableRule = { id: 5, name: "Сезон", continue_after_match: true, conditions: [], actions: [],
                                   table: { action_type: action, rows: [row] } }
      mounted?.unmount()
      mounted = mount(ParserTableCard, { props: { rule, index: 0, total: 1, isOpen: true, lines: LINES, codes: CODES } })
      const field = mounted.find("input.table-value")
      expect(field.attributes("type")).toBe("number")
      await field.setValue("4")
      expect(rule.table!.rows[0].then.value).toBe("4")
      expect(storedOf(action, rule.table!.rows).rows[0].then).toBe("4")
    }
  })

  it("puts a block picked in the one palette into the cell being edited, and a row is added and removed", async () => {
    const { w, rule } = opened([emptyRow()])
    expect(w.findAll(".pattern-palette")).toHaveLength(1)
    await w.find("tbody tr .col-if").trigger("mousedown")
    await w.findAll(".pattern-palette .palette-btn").find((each) => each.text() === t("rules.block.number.label"))!.trigger("click")
    expect(rule.table!.rows[0].if.blocks.map((block) => block.type)).toEqual(["number"])
    await w.find(".table-add").trigger("click")
    expect(rule.table!.rows).toHaveLength(2)
    await w.findAll(".row-remove")[0].trigger("click")
    expect(rule.table!.rows).toHaveLength(1)
    expect(w.findAll("tbody td.col-if")).toHaveLength(1)
  })

  it("joins the chosen cells of a column into one and splits them again, by the tools over the table", async () => {
    const { w, rule } = opened([rowOf("V", "ChinaTown"), rowOf("V", "ChineTown"), rowOf("V", "AniStar")])
    const thens = () => w.findAll("tbody td.col-then")
    const spans = (side: string) => w.findAll(`tbody td.col-${side}`).map((cell) => cell.attributes("rowspan") ?? "1")
    const tool = (name: string) => w.find(`.cell-tools .cell-${name}`)
    // Nothing chosen: there is nothing to join or to split.
    expect([tool("join").attributes("disabled"), tool("split").attributes("disabled")]).toEqual(["", ""])
    // A click chooses a cell, Shift+click another of the column: the two are chosen together, one frame around them.
    await thens()[0].trigger("mousedown")
    await thens()[1].trigger("mousedown", { shiftKey: true })
    expect(thens().map((cell) => cell.classes().filter((name) => name.startsWith("is-")).sort())).toEqual([
      ["is-first", "is-picked"], ["is-last", "is-picked"], []])
    expect(tool("join").attributes("disabled")).toBeUndefined()
    await tool("join").trigger("click")
    // One cell «ТО» of two rows with what the upper one held; it is the cell chosen now.
    expect(spans("then")).toEqual(["2", "1"])
    expect(storedOf("assign_voiceover", rule.table!.rows).rows.map((row) => row.then)).toEqual(["ChinaTown", "ChinaTown", "AniStar"])
    expect(thens()[0].classes()).toContain("is-selected")
    await w.find("td.col-then input.table-value").setValue("Чайнатаун")
    expect(rule.table!.rows.map((row) => row.then.value)).toEqual(["Чайнатаун", "Чайнатаун", "AniStar"])
    // One cell chosen: «Объединить» takes the cell below it.
    await tool("join").trigger("click")
    expect(spans("then")).toEqual(["3"])
    await tool("split").trigger("click")
    expect(spans("then")).toEqual(["1", "1", "1"])
    expect(rule.table!.rows.map((row) => row.then.value)).toEqual(["Чайнатаун", "Чайнатаун", "Чайнатаун"])
    // «ЕСЛИ» is joined the same way; rows joined in «ЕСЛИ» are not joined in «ТО» too.
    const ifs = () => w.findAll("tbody td.col-if")
    await ifs()[1].trigger("mousedown")
    await ifs()[2].trigger("mousedown", { shiftKey: true })
    await tool("join").trigger("click")
    expect(spans("if")).toEqual(["1", "2"])
    await thens()[1].trigger("mousedown")
    await thens()[2].trigger("mousedown", { shiftKey: true })
    expect(tool("join").attributes("disabled")).toBeDefined()
  })
})
