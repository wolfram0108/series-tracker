// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { mount } from "@vue/test-utils"
import VoiceOrder from "./VoiceOrder.vue"

const voices = [
  { label: "ПВА ШОУ", episodes: "1–50" },
  { label: "ruchime", episodes: "20–51" },
  { label: "AniStar", episodes: "3" },
]

describe("VoiceOrder", () => {
  it("shows the voice-overs of the season in their order with the episodes each has", () => {
    const w = mount(VoiceOrder, { props: { voices } })
    expect(w.findAll(".voice-order-item").map((item) => item.findAll("span").map((part) => part.text()))).toEqual([
      ["1", "ПВА ШОУ", "1–50"], ["2", "ruchime", "20–51"], ["3", "AniStar", "3"],
    ])
  })

  it("puts a dragged voice-over where it is dropped and asks for that order", async () => {
    const w = mount(VoiceOrder, { props: { voices } })
    const items = w.findAll(".voice-order-item")
    await items[1].trigger("dragstart")
    await items[0].trigger("dragover")
    await items[0].trigger("drop")
    expect(w.emitted("order")).toEqual([[["ruchime", "ПВА ШОУ", "AniStar"]]])
    await items[0].trigger("dragstart")
    await items[2].trigger("drop")
    expect(w.emitted("order")?.[1]).toEqual([["ruchime", "AniStar", "ПВА ШОУ"]])
  })

  it("takes no order while the series is busy", async () => {
    const w = mount(VoiceOrder, { props: { voices, busy: true } })
    const items = w.findAll(".voice-order-item")
    expect(items[0].attributes("draggable")).toBe("false")
    await items[1].trigger("dragstart")
    await items[0].trigger("drop")
    expect(w.emitted("order")).toBeUndefined()
  })
})
