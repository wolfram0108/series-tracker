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

const STUBS = ["StatusProperties", "StatusOutput", "TorrentTab", "SettingsParser", "AgentsPanel"]
// The series and, on its own path, its sources.
const answer = (sources: { id: number; url: string; source_type: string }[]) => async (path: string) =>
  ({ data: path.endsWith("/sources") ? sources : { id: 40, name: "Владыка", sources } })

describe("the status window", () => {
  it.each([["video", "https://ruchime.org/x", []], ["torrent", "https://kinozal.tv/details.php?id=1", [t("status.tab.torrent")]]])(
    "of a series of a %s source has the tabs of every series, the tab «Торрент» where a source is a torrent", async (type, url, torrent) => {
      client.GET.mockImplementation(answer([{ id: 1, url, source_type: type }]))
      mounted = mount(StatusModal, { props: { seriesId: 40 }, global: { stubs: Object.fromEntries(STUBS.map((name) => [name, true])) } })
      await flushPromises()
      expect(mounted.findAll(".st-tab").map((each) => each.attributes("title")))
        .toEqual([t("status.tab.properties"), t("output.tab"), ...torrent, t("rules.tab"), t("status.tab.agents")])
      const tab = mounted.findAll(".st-tab").find((each) => each.attributes("title") === t("rules.tab"))
      expect(mounted.find("settings-parser-stub").exists()).toBe(false) // read only when the tab is opened
      await tab!.trigger("click")
      expect(mounted.find("settings-parser-stub").exists()).toBe(true)
    })

  it("builds a tab when it is first shown and keeps it: the window opened on its properties builds no other tab", async () => {
    client.GET.mockImplementation(answer([]))
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
    client.GET.mockImplementation(answer([]))
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
    client.GET.mockImplementation(answer([]))
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
