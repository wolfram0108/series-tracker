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

  it("keeps the voice-over of the markup reference last and fixed, out of the list the user drags", async () => {
    const marked = [voices[0], voices[2], { label: "Оригинал", episodes: "1–50", online: true, last: true }]
    const w = mount(VoiceOrder, { props: { voices: marked } })
    const items = w.findAll(".voice-order-item")
    expect(items.map((item) => item.find(".voice-order-name").text())).toEqual(["ПВА ШОУ", "AniStar", "Оригинал"])
    expect(items[2].classes()).toContain("is-fixed")
    expect(items[2].find(".figure-mark").text()).toBe("3")
    w.findComponent(draggable).vm.$emit("update:modelValue", [voices[2], voices[0]])
    expect(w.emitted("order")).toEqual([[["AniStar", "ПВА ШОУ", "Оригинал"]]])
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

  describe("the name of a track", () => {
    const opened = async (props: Record<string, unknown> = {}) => {
      const w = mount(VoiceOrder, { props: { voices, ...props }, attachTo: document.body })
      await w.findAll(".voice-order-name")[1].trigger("click")
      return w
    }
    const names = (w: ReturnType<typeof mount>) => w.findAll(".voice-order-name").map((each) => each.text())

    it("is shown instead of the group's, the group said on hover", () => {
      const w = mount(VoiceOrder, { props: { voices: [{ ...voices[0], title: "ПМ_шоу" }, voices[1]] } })
      expect(names(w)).toEqual(["ПМ_шоу", "ruchime"])
      expect(w.find(".voice-order-name").attributes("title")).toContain("ПВА ШОУ")
    })

    it("is typed in the field a press on it makes, kept by Enter and shown until the core answers", async () => {
      const w = await opened()
      const field = w.find(".voice-title-input")
      expect((field.element as HTMLInputElement).value).toBe("ruchime")
      expect(document.activeElement).toBe(field.element)
      await field.setValue("  ПМ_ruchime ")
      await field.trigger("keydown", { key: "ArrowLeft" })  // the arrow keys of the field do not move the voice-over
      await field.trigger("keydown", { key: "Enter" })
      expect(w.emitted("order")).toBeUndefined()
      expect(w.emitted("title")).toEqual([["ruchime", "ПМ_ruchime"]])
      expect(w.find(".voice-title-input").exists()).toBe(false)
      expect(names(w)).toEqual(["ПВА ШОУ", "ПМ_ruchime", "AniStar"])
      w.unmount()
    })

    it("goes back to the group's when emptied or given the group's name, and Escape drops what was typed", async () => {
      const w = await opened({ voices: [voices[0], { ...voices[1], title: "ПМ_ruchime" }] })
      await w.find(".voice-title-input").setValue("")
      await w.find(".voice-title-input").trigger("blur")
      await w.findAll(".voice-order-name")[1].trigger("click")
      await w.find(".voice-title-input").setValue("другое")
      await w.find(".voice-title-input").trigger("keydown", { key: "Escape" })
      expect(w.emitted("title")).toEqual([["ruchime", ""]])
      w.unmount()
      const again = await opened({ voices: [voices[0], { ...voices[1], title: "ПМ_ruchime" }] })
      await again.find(".voice-title-input").setValue("ruchime")
      await again.find(".voice-title-input").trigger("keydown", { key: "Enter" })
      expect(again.emitted("title")).toEqual([["ruchime", ""]])
      again.unmount()
    })

    it("opens by Enter on the chip as well", async () => {
      const w = mount(VoiceOrder, { props: { voices }, attachTo: document.body })
      await w.findAll(".voice-order-item")[0].trigger("keydown", { key: "Enter" })
      expect((w.find(".voice-title-input").element as HTMLInputElement).value).toBe("ПВА ШОУ")
      w.unmount()
    })

    it("sends nothing when the name is the same, and is not opened while the series is busy", async () => {
      const w = await opened()
      await w.find(".voice-title-input").trigger("keydown", { key: "Enter" })
      expect(w.emitted("title")).toBeUndefined()
      w.unmount()
      const busy = await opened({ busy: true })
      expect(busy.find(".voice-title-input").exists()).toBe(false)
      busy.unmount()
    })
  })
})
