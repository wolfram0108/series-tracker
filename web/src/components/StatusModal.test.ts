// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"

const client = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() }))
vi.mock("../api/client", () => ({ api: client }))
vi.mock("primevue/usetoast", () => ({ useToast: () => ({ add: vi.fn() }) }))
vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} })  // the tabs fit their room (ModalShell)
Element.prototype.scrollIntoView = () => {}  // the active tab is brought into view; jsdom has no layout

import StatusModal from "./StatusModal.vue"
import AgentsPanel from "./settings/AgentsPanel.vue"
import { t } from "../i18n"

let mounted: VueWrapper | null = null
beforeEach(() => setActivePinia(createPinia()))
afterEach(() => mounted?.unmount())

const STUBS = ["StatusProperties", "StatusComposition", "StatusOutput", "StatusHistory", "SettingsParser", "AgentsPanel"]

describe("the status window", () => {
  it.each(["video", "torrent"])("of a %s series has the tab of the VK filters, the configurator of the profiles", async (kind) => {
    client.GET.mockImplementation(async () => ({ data: { id: 40, name: "Владыка", kind } }))
    mounted = mount(StatusModal, { props: { seriesId: 40 }, global: { stubs: Object.fromEntries(STUBS.map((name) => [name, true])) } })
    await flushPromises()
    const tab = mounted.findAll(".st-tab").find((each) => each.attributes("title") === t("rules.tab"))
    expect(tab).toBeDefined()
    expect(mounted.find("settings-parser-stub").exists()).toBe(false) // read only when the tab is opened
    await tab!.trigger("click")
    expect(mounted.find("settings-parser-stub").exists()).toBe(true)
  })

  it.each([["video", false], ["torrent", true]])("of a %s series has the history of releases: %s", async (kind, has) => {
    client.GET.mockImplementation(async () => ({ data: { id: 40, name: "Владыка", kind } }))
    mounted = mount(StatusModal, { props: { seriesId: 40 }, global: { stubs: Object.fromEntries(STUBS.map((name) => [name, true])) } })
    await flushPromises()
    expect(mounted.findAll(".st-tab").some((each) => each.attributes("title") === t("status.tab.history"))).toBe(has)
  })

  it("builds a tab when it is first shown and keeps it: the window opened on its properties builds no other tab", async () => {
    client.GET.mockImplementation(async () => ({ data: { id: 40, name: "Владыка", kind: "video" } }))
    mounted = mount(StatusModal, { props: { seriesId: 40 }, global: { stubs: Object.fromEntries(STUBS.map((name) => [name, true])) } })
    await flushPromises()
    const built = () => ["status-properties", "status-output", "agents-panel"]
      .filter((name) => mounted!.find(`${name}-stub`).exists())
    expect(built()).toEqual(["status-properties"])
    const open = async (title: string) => {
      await mounted!.findAll(".st-tab").find((each) => each.attributes("title") === title)!.trigger("click")
    }
    await open(t("output.tab"))
    expect(built()).toEqual(["status-properties", "status-output"])
    await open(t("status.tab.agents"))
    await open("Свойства")
    // A tab once built stays as it was left: going back to it reads nothing anew.
    expect(built()).toEqual(["status-properties", "status-output", "agents-panel"])
  })

  it("keeps the rules as they were left when another tab is open: only the close of the window ends them", async () => {
    client.GET.mockImplementation(async () => ({ data: { id: 40, name: "Владыка", kind: "video" } }))
    mounted = mount(StatusModal, { props: { seriesId: 40 }, global: { stubs: Object.fromEntries(STUBS.map((name) => [name, true])) } })
    await flushPromises()
    const open = async (title: string) => {
      await mounted!.findAll(".st-tab").find((each) => each.attributes("title") === title)!.trigger("click")
    }
    await open(t("rules.tab"))
    const rules = mounted.find("settings-parser-stub").element
    await open(t("status.tab.agents"))
    await open("Свойства")
    expect(mounted.find("settings-parser-stub").element).toBe(rules)  // the same element: not built again
    expect((rules as HTMLElement).style.display).toBe("none")
    await open(t("rules.tab"))
    expect((rules as HTMLElement).style.display).not.toBe("none")
  })

  it("has the tab of the agents of this series, built when it is first shown and kept", async () => {
    client.GET.mockImplementation(async () => ({ data: { id: 40, name: "Владыка", kind: "video" } }))
    mounted = mount(StatusModal, { props: { seriesId: 40 }, global: { stubs: Object.fromEntries(STUBS.map((name) => [name, true])) } })
    await flushPromises()
    expect(mounted.find("agents-panel-stub").exists()).toBe(false)
    const open = async (title: string) => {
      await mounted!.findAll(".st-tab").find((each) => each.attributes("title") === title)!.trigger("click")
    }
    await open(t("status.tab.agents"))
    const panel = mounted.find("agents-panel-stub")
    expect(panel.exists()).toBe(true)
    expect(mounted.findComponent(AgentsPanel).props("seriesId")).toBe(40)
    await open("Свойства")
    expect(mounted.find("agents-panel-stub").element).toBe(panel.element)
  })
})
