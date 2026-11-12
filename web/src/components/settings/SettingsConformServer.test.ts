// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"

const client = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() }))
vi.mock("../../api/client", () => ({ api: client }))
vi.mock("primevue/usetoast", () => ({ useToast: () => ({ add: vi.fn() }) }))

import PrimeVue from "primevue/config"
import SettingsConformServer from "./SettingsConformServer.vue"
import { useSSE } from "../../composables/useSSE"
import { useConformStore } from "../../stores/conform"

const PARAMS = { audio_method: "muq", drift_speed_pct: null, keep_tmp: false, fill_silence: true, extend_edges: true }
const DOMAIN = { audio_methods: ["band", "muq"], audio_default: "band", drift_min: 0.25, drift_max: 10, drift_default: 1.25 }
const view = (url: string, params: object = {}) => ({ url, work_dir: "/nas/work", work_dir_remote: "/mnt/work",
  clear_finished: false, params: { ...PARAMS, ...params }, domain: DOMAIN })

let mounted: VueWrapper | null = null

describe("the settings of the alignment server", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    Object.values(client).forEach((fn) => fn.mockReset())
  })
  afterEach(() => mounted?.unmount())

  it("are read again when the connection comes back: a save made meanwhile was not announced to the screen", async () => {
    const connected = useSSE().connected
    connected.value = true
    client.GET.mockResolvedValueOnce({ data: view("http://old.test:8787") })
    mounted = mount(SettingsConformServer, { global: { plugins: [PrimeVue] } })
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
    mounted = mount(SettingsConformServer, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    const drift = mounted.findAll("input").find((each) => each.attributes("inputmode") === "decimal")!
    expect((drift.element as HTMLInputElement).value).toBe("1.25")
    expect(mounted.find(".conform-range").text()).toBe("0.25–10")
    expect(mounted.findAll(".p-togglebutton").map((each) => each.text())).toEqual(
      ["Band", "MuQ", "Расширять до всей серии", "Только пересечение с компиляцией"])
  })

  it("writes a parameter at once, all five together, and shows what the core holds after it", async () => {
    client.GET.mockResolvedValueOnce({ data: view("http://d.test:8787") })
    mounted = mount(SettingsConformServer, { global: { plugins: [PrimeVue] } })
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
    mounted = mount(SettingsConformServer, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    client.PUT.mockResolvedValueOnce({ data: { saved: false, settings: view("http://d.test:8787"), problems: [
      { field: "drift_speed_pct", detail: { code: "conform_drift_out_of_range", params: { value: "40", low: "0.25", high: "10" } } }] } })
    const drift = mounted.findAll("input").find((each) => each.attributes("inputmode") === "decimal")!
    await drift.setValue("40")
    await drift.trigger("change")
    await flushPromises()
    expect((drift.element as HTMLInputElement).value).toBe("1.25")
    expect(mounted.find(".problem").text()).toContain("40")
  })

  it("keeps two quick changes: the second save carries both and an earlier answer takes nothing back", async () => {
    client.GET.mockResolvedValueOnce({ data: view("http://d.test:8787") })
    mounted = mount(SettingsConformServer, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    let first: (value: unknown) => void = () => {}
    client.PUT.mockReturnValueOnce(new Promise((resolve) => { first = resolve }))
    client.PUT.mockResolvedValueOnce({ data: { saved: true, problems: [],
      settings: view("http://d.test:8787", { keep_tmp: true, extend_edges: false }) } })
    const switches = mounted.findAll(".conform-switch input")
    await switches[0].setValue(true)
    const cut = mounted.findAll(".p-togglebutton").find((each) => each.text() === "Только пересечение с компиляцией")!
    await cut.trigger("click")
    first({ data: { saved: true, problems: [], settings: view("http://d.test:8787", { keep_tmp: true }) } })
    await flushPromises()

    expect(client.PUT.mock.calls.map((call) => call[1].body)).toEqual([
      { ...PARAMS, keep_tmp: true }, { ...PARAMS, keep_tmp: true, extend_edges: false }])
    expect(cut.attributes("aria-pressed")).toBe("true")
  })

  it("does not take an announcement older than the view it shows", async () => {
    client.GET.mockResolvedValueOnce({ data: { ...view("http://d.test:8787", { extend_edges: false }), revision: 3 } })
    mounted = mount(SettingsConformServer, { global: { plugins: [PrimeVue] } })
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
})

