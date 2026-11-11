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
    expect(w.find(".col-then .selected-value").text()).toBe(t("rules.action.assign_voiceover"))
    expect(w.find(".rule-rows").text()).toBe(t("rules.table.rows", { count: 1 }))
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
      expect(storedOf(action, rule.table!.rows, false).rows[0].then).toBe("4")
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

  it("joins the cell being edited with the one below and splits it again, by the tools of the cell", async () => {
    const { w, rule } = opened([rowOf("V", "ChinaTown"), rowOf("V", "ChineTown"), rowOf("V", "AniStar")])
    const cells = () => w.findAll("tbody td.col-then").map((cell) => cell.attributes("rowspan") ?? "1")
    expect(cells()).toEqual(["1", "1", "1"])
    await w.findAll("tbody td.col-then")[0].trigger("mousedown")
    await w.find("td.col-then .cell-join").trigger("click")
    // One cell «ТО» of two rows: the second row has no cell of its own, and its value is the joined one.
    expect(cells()).toEqual(["2", "1"])
    expect(storedOf("assign_voiceover", rule.table!.rows, false).rows.map((row) => row.then)).toEqual(["ChinaTown", "ChinaTown", "AniStar"])
    await w.find("td.col-then input.table-value").setValue("Чайнатаун")
    expect(rule.table!.rows.map((row) => row.then.value)).toEqual(["Чайнатаун", "Чайнатаун", "AniStar"])
    await w.find("td.col-then .cell-split").trigger("click")
    expect(cells()).toEqual(["1", "1", "1"])
    // «ЕСЛИ» is joined the same way: one condition with two «ТО».
    await w.findAll("tbody td.col-if")[1].trigger("mousedown")
    await w.find("td.col-if .cell-join").trigger("click")
    expect(w.findAll("tbody td.col-if").map((cell) => cell.attributes("rowspan") ?? "1")).toEqual(["1", "2"])
    // The rows joined in «ЕСЛИ» cannot be joined in «ТО» too.
    await w.findAll("tbody td.col-then")[1].trigger("mousedown")
    expect(w.find("td.col-then .cell-join").attributes("disabled")).toBeDefined()
  })
})
