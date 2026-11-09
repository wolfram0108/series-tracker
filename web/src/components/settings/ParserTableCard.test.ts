// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest"
import { mount, type VueWrapper } from "@vue/test-utils"
import ParserTableCard from "./ParserTableCard.vue"
import type { EditableRule } from "./ParserRuleCard.vue"
import { emptyRow, rowOf } from "../../lib/ruleTable"
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
    expect(rule.table!.rows.map((row) => [row.ifBlocks[0].value, row.thenValue])).toEqual([
      ["V:AniStar", "AniStar"], ["V:AnimeVost", "AnimeVost"], ["V:ChinaTown", "ChinaTown"]])
    expect(w.findAll("tbody tr.is-fresh")).toHaveLength(2)
    expect(chip(w, "V").text()).toBe("V:0")
    expect(chip(w, "V").attributes("disabled")).toBeDefined()
  })

  it("puts a block picked in the one palette into the cell being edited, and a row is added and removed", async () => {
    const { w, rule } = opened([emptyRow()])
    expect(w.findAll(".pattern-palette")).toHaveLength(1)
    await w.find("tbody tr .col-if").trigger("mousedown")
    await w.findAll(".pattern-palette .palette-btn").find((each) => each.text() === t("rules.block.number.label"))!.trigger("click")
    expect(rule.table!.rows[0].ifBlocks.map((block) => block.type)).toEqual(["number"])
    await w.find(".table-add").trigger("click")
    expect(rule.table!.rows).toHaveLength(2)
    await w.findAll(".row-remove")[0].trigger("click")
    expect(rule.table!.rows).toHaveLength(1)
    expect(w.findAll("tbody td.col-if")).toHaveLength(1)
  })
})
