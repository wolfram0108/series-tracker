// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import PrimeVue from "primevue/config"
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

  describe("the episodes of a track", () => {
    const FULL = "1–154, 158–162, 165–168, 170–174, 176–656"
    const gapped = [{ label: "ruchime", episodes: FULL, online: true }, voices[0]]
    const mounted = () => mount(VoiceOrder, { props: { voices: gapped }, global: { plugins: [PrimeVue] }, attachTo: document.body })

    it("are one short line, from the first to the last, however many gaps lie between them", () => {
      const w = mounted()
      expect(w.findAll(".voice-order-episodes").map((each) => each.text())).toEqual(["1–656", "1–50"])
      expect(w.text()).not.toContain("158–162")
      w.unmount()
    })

    it("are dotted, and a stop for the keyboard, only where there are gaps", () => {
      const w = mounted()
      const reach = w.findAll(".voice-order-episodes")
      expect(reach.map((each) => each.classes().includes("has-more"))).toEqual([true, false])
      expect(reach.map((each) => each.attributes("tabindex"))).toEqual(["0", undefined])
      w.unmount()
    })

    it("show what is there and what is missing in a card by hover and by focus, and take it away with the pointer", async () => {
      const w = mounted()
      const reach = w.find(".voice-order-episodes")
      expect(document.body.querySelector(".hover-note")).toBeNull()
      await reach.trigger("mouseenter")
      await flushPromises()
      const card = document.body.querySelector(".hover-note")
      expect(card?.textContent).toContain(FULL)
      expect(card?.textContent).toContain("155–157, 163–164, 169, 175")
      expect(card?.textContent).toMatch(/Есть.*Пропуски/)
      await reach.trigger("mouseleave")
      await flushPromises()
      await new Promise((done) => setTimeout(done, 50))
      expect(document.body.querySelector(".hover-note")).toBeNull()
      await reach.trigger("focus")
      await flushPromises()
      expect(document.body.querySelector(".hover-note")?.textContent).toContain(FULL)
      w.unmount()
    })

    it("keep the whole list for the reader of the screen, and the whole name in the hint of a clipped one", () => {
      const w = mount(VoiceOrder, { props: { voices: [{ label: "Dub", title: "A very long name of a track", episodes: FULL, online: true }] },
        global: { plugins: [PrimeVue] } })
      expect(w.find(".voice-order-item").attributes("aria-label")).toContain(FULL)
      expect(w.find(".voice-order-name").attributes("title")?.split("\n")[0]).toBe("A very long name of a track")
    })
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
