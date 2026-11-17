// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount, type DOMWrapper, type VueWrapper } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"

const client = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() }))
vi.mock("../../api/client", () => ({ api: client }))
vi.mock("primevue/usetoast", () => ({ useToast: () => ({ add: vi.fn() }) }))

import PrimeVue from "primevue/config"
import ExtensionConform from "./ExtensionConform.vue"
import { useSSE } from "../../composables/useSSE"
import { useConformStore } from "../../stores/conform"

const PARAMS = { audio_method: "muq", drift_speed_pct: null, keep_tmp: false, fill_silence: true, extend_edges: true }
const DOMAIN = { audio_methods: ["band", "muq"], audio_default: "band", drift_min: 0.25, drift_max: 10, drift_default: 1.25 }
const view = (url: string, params: object = {}) => ({ url, work_dir: "/nas/work", work_dir_remote: "/mnt/work",
  clear_finished: false, params: { ...PARAMS, ...params }, domain: DOMAIN })

let mounted: VueWrapper | null = null

// Typing as the browser does it: the value goes in with `input`; `change` comes later, on Enter or when the field is left.
async function type(field: DOMWrapper<Element>, value: string) {
  ;(field.element as HTMLInputElement).value = value
  await field.trigger("input")
}

describe("the tab of the alignment server", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    // A call a test does not answer gets an empty answer, as a core that says nothing back, not a missing one.
    Object.values(client).forEach((fn) => fn.mockReset().mockResolvedValue({}))
  })
  afterEach(() => mounted?.unmount())

  it("are read again when the connection comes back: a save made meanwhile was not announced to the screen", async () => {
    const connected = useSSE().connected
    connected.value = true
    client.GET.mockResolvedValueOnce({ data: view("http://old.test:8787") })
    mounted = mount(ExtensionConform, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    const shown = () => (mounted!.find("input").element as HTMLInputElement).value
    expect(shown()).toBe("http://old.test:8787")

    connected.value = false
    await flushPromises()
    client.GET.mockResolvedValueOnce({ data: view("http://new.test:8787") })
    connected.value = true
    await flushPromises()
    expect(client.GET).toHaveBeenCalledTimes(2)
    expect(shown()).toBe("http://new.test:8787")
    connected.value = false
  })

  it("shows how the daemon is asked to align, with the drift of the daemon when none is chosen", async () => {
    client.GET.mockResolvedValueOnce({ data: view("http://d.test:8787") })
    mounted = mount(ExtensionConform, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    const drift = mounted.findAll("input").find((each) => each.attributes("inputmode") === "decimal")!
    expect((drift.element as HTMLInputElement).value).toBe("1.25")
    expect(mounted.find(".item-hint").text()).toBe("0.25–10")
    expect(mounted.findAll(".p-togglebutton").map((each) => each.text())).toEqual(
      ["Band", "MuQ", "Расширять до всей серии", "Только пересечение с компиляцией"])
  })

  it("writes a parameter at once, all five together, and shows what the core holds after it", async () => {
    client.GET.mockResolvedValueOnce({ data: view("http://d.test:8787") })
    mounted = mount(ExtensionConform, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    client.PUT.mockResolvedValueOnce({ data: { saved: true, problems: [], settings: view("http://d.test:8787", { drift_speed_pct: 2.5 }) } })
    const drift = mounted.findAll("input").find((each) => each.attributes("inputmode") === "decimal")!
    await drift.setValue("2,5")
    await drift.trigger("change")
    await flushPromises()
    expect(client.PUT).toHaveBeenCalledWith("/api/conform/params", { body: { ...PARAMS, drift_speed_pct: 2.5 } })
    expect((drift.element as HTMLInputElement).value).toBe("2.5")
  })

  it("goes back to what the core holds when it refuses a parameter, and shows why", async () => {
    client.GET.mockResolvedValueOnce({ data: view("http://d.test:8787") })
    mounted = mount(ExtensionConform, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    client.PUT.mockResolvedValueOnce({ data: { saved: false, settings: view("http://d.test:8787"), problems: [
      { field: "drift_speed_pct", detail: { code: "conform_drift_out_of_range", params: { value: "40", low: "0.25", high: "10" } } }] } })
    const drift = mounted.findAll("input").find((each) => each.attributes("inputmode") === "decimal")!
    await drift.setValue("40")
    await drift.trigger("change")
    await flushPromises()
    expect((drift.element as HTMLInputElement).value).toBe("1.25")
    expect(mounted.find(".sheet-problem").text()).toContain("40")
  })

  it("keeps two quick changes: the second save carries both and an earlier answer takes nothing back", async () => {
    client.GET.mockResolvedValueOnce({ data: view("http://d.test:8787") })
    mounted = mount(ExtensionConform, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    let first: (value: unknown) => void = () => {}
    client.PUT.mockReturnValueOnce(new Promise((resolve) => { first = resolve }))
    client.PUT.mockResolvedValueOnce({ data: { saved: true, problems: [],
      settings: view("http://d.test:8787", { keep_tmp: true, extend_edges: false }) } })
    const switches = mounted.findAll("input[role='switch']")
    await switches[0].setValue(true)
    const cut = mounted.findAll(".p-togglebutton").find((each) => each.text() === "Только пересечение с компиляцией")!
    await cut.trigger("click")
    first({ data: { saved: true, problems: [], settings: view("http://d.test:8787", { keep_tmp: true }) } })
    await flushPromises()

    expect(client.PUT.mock.calls.map((call) => call[1].body)).toEqual([
      { ...PARAMS, keep_tmp: true }, { ...PARAMS, keep_tmp: true, extend_edges: false }])
    expect(cut.attributes("aria-pressed")).toBe("true")
  })

  it("keeps a drift typed while an earlier save is on its way: the answer to that save takes nothing but what it carried", async () => {
    client.GET.mockResolvedValueOnce({ data: view("http://d.test:8787") })
    mounted = mount(ExtensionConform, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    let answer: (value: unknown) => void = () => {}
    client.PUT.mockReturnValueOnce(new Promise((resolve) => { answer = resolve }))
    await mounted.findAll(".p-togglebutton").find((each) => each.text() === "Band")!.trigger("click")
    const drift = mounted.findAll("input").find((each) => each.attributes("inputmode") === "decimal")!
    await type(drift, "2,5")                              // typed meanwhile
    answer({ data: { saved: true, problems: [], settings: view("http://d.test:8787", { audio_method: "band" }) } })
    await flushPromises()
    expect((drift.element as HTMLInputElement).value).toBe("2,5")
    client.PUT.mockResolvedValueOnce({ data: { saved: true, problems: [],
      settings: view("http://d.test:8787", { audio_method: "band", drift_speed_pct: 2.5 }) } })
    await drift.trigger("change")
    await flushPromises()
    expect(client.PUT).toHaveBeenLastCalledWith("/api/conform/params", { body: { ...PARAMS, audio_method: "band", drift_speed_pct: 2.5 } })
    expect((drift.element as HTMLInputElement).value).toBe("2.5")
  })

  it("keeps an address being typed when the core announces something meanwhile, and takes the announcement in a field left alone", async () => {
    client.GET.mockResolvedValueOnce({ data: { ...view("http://d.test:8787"), revision: 1 } })
    mounted = mount(ExtensionConform, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    const [address, here] = mounted.findAll("input")
    await type(address, "http://typed.test:8787")
    useConformStore().applySettings({ ...view("http://d.test:8787"), work_dir: "/nas/other", revision: 2 } as never)
    await flushPromises()
    expect((address.element as HTMLInputElement).value).toBe("http://typed.test:8787")
    expect((here.element as HTMLInputElement).value).toBe("/nas/other")
    client.PUT.mockResolvedValueOnce({ data: { saved: true, problems: [], settings: { ...view("http://typed.test:8787"), work_dir: "/nas/other", revision: 3 } } })
    await address.trigger("change")
    await flushPromises()
    expect(client.PUT).toHaveBeenCalledWith("/api/conform/settings", {
      body: { url: "http://typed.test:8787", work_dir: "/nas/other", work_dir_remote: "/mnt/work" } })
  })

  it("does not take an announcement older than the view it shows", async () => {
    client.GET.mockResolvedValueOnce({ data: { ...view("http://d.test:8787", { extend_edges: false }), revision: 3 } })
    mounted = mount(ExtensionConform, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    const cut = () => mounted!.findAll(".p-togglebutton").find((each) => each.text() === "Только пересечение с компиляцией")!
    expect(cut().attributes("aria-pressed")).toBe("true")

    useConformStore().applySettings({ ...view("http://d.test:8787"), revision: 2 } as never)
    await flushPromises()
    expect(cut().attributes("aria-pressed")).toBe("true")
    useConformStore().applySettings({ ...view("http://d.test:8787"), revision: 4 } as never)
    await flushPromises()
    expect(cut().attributes("aria-pressed")).toBe("false")
  })

  it("shows the line of the address and nothing else while no address is named, and the rest as it is typed", async () => {
    client.GET.mockResolvedValueOnce({ data: { ...view(""), work_dir: "", work_dir_remote: "" } })
    mounted = mount(ExtensionConform, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    expect(mounted.findAll("input")).toHaveLength(1)
    expect(mounted.text()).not.toContain("Метод звука")
    await mounted.find("input").setValue("http://d.test:8787")
    expect(mounted.text()).toContain("Метод звука")
    expect(mounted.findAll("input").length).toBeGreaterThan(3)
  })

  it("writes the flag that finished jobs leave the queue and reads the settings again", async () => {
    client.GET.mockResolvedValueOnce({ data: view("http://d.test:8787") })
    mounted = mount(ExtensionConform, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    client.POST.mockResolvedValueOnce({ data: { enabled: true } })
    client.GET.mockResolvedValueOnce({ data: { ...view("http://d.test:8787"), clear_finished: true } })
    const clear = mounted.findAll("input[role='switch']")[2]
    await clear.setValue(true)
    await flushPromises()
    expect(client.POST).toHaveBeenCalledWith("/api/settings/conform_clear_finished", { body: { enabled: true } })
    expect((mounted.findAll("input[role='switch']")[2].element as HTMLInputElement).checked).toBe(true)
  })
})
