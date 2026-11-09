// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { mount } from "@vue/test-utils"
import draggable from "vuedraggable"
import VoiceOrder from "./VoiceOrder.vue"

const voices = [
  { label: "ПВА ШОУ", episodes: "1–50", online: true },
  { label: "ruchime", episodes: "20–51", online: true },
  { label: "AniStar", episodes: "3", online: false },
]

describe("VoiceOrder", () => {
  it("shows the voice-overs of the season in their order with the episodes each has", () => {
    const w = mount(VoiceOrder, { props: { voices } })
    expect(w.findAll(".voice-order-item").map((item) => item.findAll("span").map((part) => part.text()))).toEqual([
      ["1", "ПВА ШОУ", "1–50"], ["2", "ruchime", "20–51"], ["3", "AniStar", "3"],
    ])
  })

  it("tells on the number of each voice-over whether a source gives it or only the files hold it", () => {
    const w = mount(VoiceOrder, { props: { voices } })
    expect(w.findAll(".figure-mark").map((mark) => mark.classes().filter((name) => name.startsWith("is-")))).toEqual([
      ["is-online"], ["is-online"], ["is-offline"],
    ])
  })

  it("is the list of the constructor's engine and asks for the order a voice-over is dragged into", async () => {
    const w = mount(VoiceOrder, { props: { voices } })
    expect(w.findComponent({ name: "OrderList" }).exists()).toBe(true)
    w.findComponent(draggable).vm.$emit("update:modelValue", [voices[1], voices[0], voices[2]])
    expect(w.emitted("order")).toEqual([[["ruchime", "ПВА ШОУ", "AniStar"]]])
  })

  it("moves a voice-over with the arrow keys", async () => {
    const w = mount(VoiceOrder, { props: { voices } })
    await w.findAll(".voice-order-item")[2].trigger("keydown", { key: "ArrowLeft" })
    expect(w.emitted("order")).toEqual([[["ПВА ШОУ", "AniStar", "ruchime"]]])
  })

  it("takes no order while the series is busy", async () => {
    const w = mount(VoiceOrder, { props: { voices, busy: true } })
    w.findComponent(draggable).vm.$emit("update:modelValue", [voices[1], voices[0], voices[2]])
    await w.findAll(".voice-order-item")[1].trigger("keydown", { key: "ArrowLeft" })
    expect(w.emitted("order")).toBeUndefined()
  })
})
