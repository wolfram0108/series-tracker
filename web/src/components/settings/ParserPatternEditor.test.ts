// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { mount } from "@vue/test-utils"
import { t } from "../../i18n"
import ParserPatternEditor from "./ParserPatternEditor.vue"
import type { PatternBlock } from "../../lib/patternBlocks"

const text = (id: string, value: string): PatternBlock => ({ id, type: "text", value })
const or = (id: string): PatternBlock => ({ id, type: "or" })

describe("the block «или» of the constructor", () => {
  it("is in the palette of ЕСЛИ and of ТО", () => {
    for (const context of ["if", "then"] as const) {
      const w = mount(ParserPatternEditor, { props: { blocks: [], context } })
      expect(w.findAll(".pattern-palette .palette-btn").map((each) => each.text())).toContain(t("rules.block.or.label"))
    }
  })

  it("glues the texts it joins into one unit, a chain of three as well", () => {
    const blocks = [text("a", "вечная воля"), or("o1"), text("b", "воля вечная"), or("o2"), text("c", "Eternal Will")]
    const w = mount(ParserPatternEditor, { props: { blocks, context: "if" } })
    const drawn = w.findAll(".pattern-blocks-container .pattern-block")
    const joins = drawn.map((each) => [each.classes("is-glued"), each.classes("carries-attachment")])
    expect(joins).toEqual([[false, true], [true, true], [true, true], [true, true], [true, false]])
    expect(drawn.some((each) => each.classes("is-dangling"))).toBe(false)
  })

  it("marks an «или» that waits for its second text, and says why", () => {
    const w = mount(ParserPatternEditor, { props: { blocks: [text("a", "вечная воля"), or("o")], context: "then" } })
    const [first, dangling] = w.findAll(".pattern-blocks-container .pattern-block")
    expect(dangling.classes("is-dangling")).toBe(true)
    expect(dangling.attributes("title")).toBe(t("rules.block.or.dangling"))
    expect(first.classes("carries-attachment")).toBe(false)
  })
})
