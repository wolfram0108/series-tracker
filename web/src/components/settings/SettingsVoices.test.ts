// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import draggable from "vuedraggable"

const client = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() }))
vi.mock("../../api/client", () => ({ api: client }))
vi.mock("primevue/usetoast", () => ({ useToast: () => ({ add: vi.fn() }) }))

import SettingsVoices from "./SettingsVoices.vue"
import { useSSE } from "../../composables/useSSE"

const view = (priority: string[]) => ({
  priority, default_priority: ["kodik", "vibix", "allvideo"], voice_groups: {}, rejected: [], cut_max_height: 720,
})

// What the core answers: the settings and where each player is.
const core: { settings: object; players: object } = { settings: {}, players: { players: [] } }
const place = (name: string, label: string, sources: number) => ({ name, label, sources })
const PLAYERS = { players: [
  { name: "kodik", label: "Kodik", kind: "site", height: 720, places: [place("yummyani", "yummyani", 10), place("ruchime", "ruchime", 4)] },
  { name: "vibix", label: "Vibix", kind: "site", height: null, places: [] },
  { name: "line:zijianm3u8", label: "zijianm3u8", kind: "line", height: 2160,
    places: [place("huayi", "华谊 (Huayi)", 6), place("appmn", "米诺 (AppMN)", 0)] },
] }

let mounted: VueWrapper | null = null
async function opened(priority: string[], extra: object = {}): Promise<VueWrapper> {
  core.settings = { ...view(priority), ...extra }
  client.GET.mockImplementation((path: string) =>
    Promise.resolve({ data: path === "/api/voices/players" ? core.players : core.settings }))
  mounted = mount(SettingsVoices, { global: { stubs: { VoiceGroupsEditor: true } } })
  await flushPromises()
  return mounted
}
const shown = (w: VueWrapper): string[] => w.findAll(".priority-row .priority-name").map((each) => each.text())
// The answer of the core held until the test gives it, so what the list shows meanwhile is seen.
function held() {
  let give: (value: unknown) => void = () => {}
  client.PUT.mockReturnValueOnce(new Promise((resolve) => { give = resolve }))
  return (value: unknown) => give(value)
}

describe("the order of the players in the settings of the voices", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    Object.values(client).forEach((fn) => fn.mockReset())
    core.players = { players: [] }
  })
  afterEach(() => mounted?.unmount())

  it("is a list of the constructor's engine, each player with a handle", async () => {
    const w = await opened(["kodik", "vibix", "allvideo"])
    expect(shown(w)).toEqual(["kodik", "vibix", "allvideo"])
    expect(w.findAll(".priority-row").map((row) => row.attributes("data-draggable"))).toEqual(["true", "true", "true"])
    expect(w.findAll(".priority-row .order-handle")).toHaveLength(3)
  })

  it("sends the order a player is dragged into and keeps it on the screen until the core answers", async () => {
    const w = await opened(["kodik", "vibix", "allvideo"])
    const answer = held()
    w.findComponent(draggable).vm.$emit("update:modelValue", ["allvideo", "kodik", "vibix"])
    await flushPromises()
    expect(client.PUT.mock.calls[0][1].body.priority).toEqual(["allvideo", "kodik", "vibix"])
    expect(shown(w)).toEqual(["allvideo", "kodik", "vibix"])
    answer({ data: { saved: true, problems: [], settings: view(["allvideo", "kodik", "vibix"]) } })
    await flushPromises()
    expect(shown(w)).toEqual(["allvideo", "kodik", "vibix"])
  })

  it("moves a player with the arrow keys on its handle", async () => {
    const w = await opened(["kodik", "vibix", "allvideo"])
    held()
    await w.findAll(".priority-row .order-handle")[1].trigger("keydown", { key: "ArrowUp" })
    expect(client.PUT.mock.calls[0][1].body.priority).toEqual(["vibix", "kodik", "allvideo"])
  })

  it("goes back to the order the core holds when the core refuses the new one", async () => {
    const w = await opened(["kodik", "vibix", "allvideo"])
    const answer = held()
    await w.findAll(".priority-row .order-handle")[2].trigger("keydown", { key: "ArrowUp" })
    expect(shown(w)).toEqual(["kodik", "allvideo", "vibix"])
    answer({ data: { saved: false, problems: [], settings: { ...view(["kodik", "vibix", "allvideo"]), rejected: ["priority"] } } })
    await flushPromises()
    expect(shown(w)).toEqual(["kodik", "vibix", "allvideo"])
  })

  it("goes back to the order the core holds when the answer does not come", async () => {
    const w = await opened(["kodik", "vibix", "allvideo"])
    const answer = held()
    await w.findAll(".priority-row .order-handle")[1].trigger("keydown", { key: "ArrowDown" })
    expect(shown(w)).toEqual(["kodik", "allvideo", "vibix"])
    answer({ error: { error: "network" } })
    await flushPromises()
    expect(shown(w)).toEqual(["kodik", "vibix", "allvideo"])
  })

  it("reads the settings again when the connection comes back: a save made meanwhile was not announced to it", async () => {
    const connected = useSSE().connected
    connected.value = true
    const w = await opened(["kodik", "vibix", "allvideo"])
    connected.value = false
    await flushPromises()
    core.settings = view(["allvideo", "kodik", "vibix"])
    connected.value = true
    await flushPromises()
    expect(client.GET.mock.calls.filter(([path]) => path === "/api/voices/settings")).toHaveLength(2)
    expect(shown(w)).toEqual(["allvideo", "kodik", "vibix"])
    connected.value = false
  })

  it("shows of each player its kind, the best height of its rows and where it is with how many sources", async () => {
    core.players = PLAYERS
    const w = await opened(["kodik", "vibix", "line:zijianm3u8"])
    const rows = w.findAll(".priority-row")
    const places = (index: number) => rows[index].findAll(".place").map((each) => [each.text(), each.classes("zero")])
    expect(rows.map((row) => row.find(".kind").text())).toEqual(["Плеер сайта", "Плеер сайта", "Линия"])
    expect(rows.map((row) => row.find(".height-cell").exists() ? row.find(".height-cell").text() : "")).toEqual(["720p", "", "2160p"])
    expect(places(0)).toEqual([["yummyani 10", false], ["ruchime 4", false]])
    expect(rows[1].find(".none").text()).toBe("нет источников")
    expect(places(2)).toEqual([["华谊 (Huayi) 6", false], ["米诺 (AppMN) 0", true]])
  })

  it("switches a player off and on for every source, keeping its place in the order", async () => {
    core.players = PLAYERS
    const w = await opened(["kodik", "vibix", "line:zijianm3u8"], { players_off: ["vibix"] })
    expect(w.findAll(".priority-row").map((row) => row.classes("is-off"))).toEqual([false, true, false])
    held()
    w.findAll(".priority-row")[0].findComponent({ name: "ToggleSwitch" }).vm.$emit("update:modelValue", false)
    await flushPromises()
    expect(client.PUT.mock.calls[0][1].body.players_off).toEqual(["vibix", "kodik"])
    expect(client.PUT.mock.calls[0][1].body.priority).toEqual(["kodik", "vibix", "line:zijianm3u8"])
  })

  it("switches a player back on", async () => {
    const w = await opened(["kodik", "vibix"], { players_off: ["vibix", "kodik"] })
    held()
    w.findAll(".priority-row")[1].findComponent({ name: "ToggleSwitch" }).vm.$emit("update:modelValue", true)
    await flushPromises()
    expect(client.PUT.mock.calls[0][1].body.players_off).toEqual(["kodik"])
  })
})
