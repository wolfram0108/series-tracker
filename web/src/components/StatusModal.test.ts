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
import { t } from "../i18n"

let mounted: VueWrapper | null = null
beforeEach(() => setActivePinia(createPinia()))
afterEach(() => mounted?.unmount())

const STUBS = ["StatusProperties", "StatusComposition", "StatusOutput", "StatusSlicing", "StatusHistory", "SettingsParser"]

describe("the status window", () => {
  it.each(["video", "torrent"])("of a %s series has the tab of the VK filters, the configurator of the profiles", async (kind) => {
    client.GET.mockImplementation(async () => ({ data: { id: 40, name: "Владыка", kind } }))
    mounted = mount(StatusModal, { props: { seriesId: 40 }, global: { stubs: Object.fromEntries(STUBS.map((name) => [name, true])) } })
    await flushPromises()
    const tab = mounted.findAll(".st-tab").find((each) => each.attributes("title") === t("status.tab.parser"))
    expect(tab).toBeDefined()
    expect(mounted.find("settings-parser-stub").exists()).toBe(false) // read only when the tab is opened
    await tab!.trigger("click")
    expect(mounted.find("settings-parser-stub").exists()).toBe(true)
  })

  it("builds a tab when it is first shown and keeps it: the window opened on its properties builds no other tab", async () => {
    client.GET.mockImplementation(async () => ({ data: { id: 40, name: "Владыка", kind: "video" } }))
    mounted = mount(StatusModal, { props: { seriesId: 40 }, global: { stubs: Object.fromEntries(STUBS.map((name) => [name, true])) } })
    await flushPromises()
    const built = () => ["status-properties", "status-output", "status-slicing", "status-history"]
      .filter((name) => mounted!.find(`${name}-stub`).exists())
    expect(built()).toEqual(["status-properties"])
    const open = async (title: string) => {
      await mounted!.findAll(".st-tab").find((each) => each.attributes("title") === title)!.trigger("click")
    }
    await open(t("output.tab"))
    expect(built()).toEqual(["status-properties", "status-output"])
    await open("История")
    await open("Свойства")
    // A tab once built stays as it was left: going back to it reads nothing anew.
    expect(built()).toEqual(["status-properties", "status-output", "status-history"])
  })
})
