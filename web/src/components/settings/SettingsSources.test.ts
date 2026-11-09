// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"

const client = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() }))
vi.mock("../../api/client", () => ({ api: client }))
vi.mock("primevue/usetoast", () => ({ useToast: () => ({ add: vi.fn() }) }))

import SettingsSources from "./SettingsSources.vue"
import { useSSE } from "../../composables/useSSE"
import { t } from "../../i18n"
import { useSourcesStore, type SourcesSettingsView } from "../../stores/sources"

let mounted: VueWrapper | null = null
beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(client).forEach((fn) => fn.mockReset())
})
afterEach(() => mounted?.unmount())

function view(rutracker: string[]): SourcesSettingsView {
  return {
    trackers: [{ id: 3, display_name: "Kinozal.me", mirrors: ["kinozal.me"] },
               { id: 5, display_name: "RuTracker.org", mirrors: rutracker }],
    readers: [{ name: "Kinozal.me", kind: "tracker", domains: ["kinozal.me", "kinozal.guru"] },
              { name: "maccms", kind: "site", domains: ["jszyapi.com", "lziapi.com"] }],
  }
}

async function opened() {
  client.GET.mockImplementation(async () => ({ data: view(["rutracker.org"]) }))
  client.PUT.mockImplementation(async () => ({ data: { success: true } }))
  mounted = mount(SettingsSources)
  await flushPromises()
  return mounted
}
async function choose(w: VueWrapper, label: string) {
  await w.find(".sources-editor .item-select").trigger("click")
  ;([...document.body.querySelectorAll(".options-list .option")].find((each) => each.textContent?.trim() === label) as HTMLElement).click()
  await flushPromises()
}
const mirrors = (w: VueWrapper) => w.findAll(".sources-editor .mirror-pill").map((pill) => pill.text())

describe("the tab «Источники»", () => {
  it("is one row choosing a tracker; the mirrors of the chosen one lie under it, each change handed to the core at once", async () => {
    const w = await opened()
    expect(w.find(".sources-editor .mirror-pill").exists()).toBe(false)
    await choose(w, "RuTracker.org")
    expect(mirrors(w)).toEqual(["rutracker.org"])

    await w.find(".sources-editor input").setValue("https://rutracker.net/forum")
    await w.find(".sources-editor .btn-add").trigger("click")
    await flushPromises()
    expect(client.PUT).toHaveBeenLastCalledWith("/api/trackers/{tracker_id}",
      { params: { path: { tracker_id: 5 } }, body: { mirrors: ["rutracker.org", "rutracker.net"] } })
    // What the core took is shown by its announcement, as in every other open window.
    useSourcesStore().applySettings(view(["rutracker.org", "rutracker.net"]))
    await flushPromises()
    expect(mirrors(w)).toEqual(["rutracker.org", "rutracker.net"])

    await w.find(".sources-editor .mirror-pill-remove").trigger("click")
    await flushPromises()
    expect(client.PUT).toHaveBeenLastCalledWith("/api/trackers/{tracker_id}",
      { params: { path: { tracker_id: 5 } }, body: { mirrors: ["rutracker.net"] } })
    await choose(w, "Kinozal.me")
    expect(mirrors(w)).toEqual(["kinozal.me"])
  })

  it("shows a save made in another window as the core announces it, and reads itself again when the connection is back", async () => {
    const w = await opened()
    await choose(w, "RuTracker.org")
    useSourcesStore().applySettings(view(["rutracker.org", "rutracker.one"]))
    await flushPromises()
    expect(mirrors(w)).toEqual(["rutracker.org", "rutracker.one"])

    const connected = useSSE().connected
    connected.value = false
    await flushPromises()
    client.GET.mockImplementation(async () => ({ data: view(["rutracker.two"]) }))
    connected.value = true
    await flushPromises()
    expect(mirrors(w)).toEqual(["rutracker.two"])
  })

  it("shows what the core holds when it refuses a change", async () => {
    const w = await opened()
    await choose(w, "Kinozal.me")
    client.PUT.mockImplementation(async () => ({ error: { error: "refused" } }))
    await w.find(".sources-editor .mirror-pill-remove").trigger("click")
    await flushPromises()
    expect(mirrors(w)).toEqual(["kinozal.me"])
    expect(client.GET).toHaveBeenLastCalledWith("/api/sources/settings")
  })

  it("ends with every domain the service reads pages of, as the core lists them", async () => {
    const w = await opened()
    const section = w.find(".parsed-domains")
    expect(section.find(".fieldset-header").text()).toBe(t("settings.sources.domains.title"))
    expect(section.findAll(".domains-row").map((row) => row.findAll(".mirror-pill").map((pill) => pill.text()))).toEqual([
      ["kinozal.me", "kinozal.guru"], ["jszyapi.com", "lziapi.com"]])
    expect(section.findAll(".domains-kind").map((each) => each.text())).toEqual([
      t("settings.sources.domains.kind.tracker"), t("settings.sources.domains.kind.site")])
    // Read only: a domain of this list has nothing to remove it by.
    expect(section.find(".mirror-pill-remove").exists()).toBe(false)
  })
})
