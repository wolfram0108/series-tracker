// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils"

const client = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() }))
vi.mock("../../api/client", () => ({ api: client }))
vi.mock("primevue/usetoast", () => ({ useToast: () => ({ add: vi.fn() }) }))

import SettingsSources from "./SettingsSources.vue"
import { t } from "../../i18n"

let mounted: VueWrapper | null = null
afterEach(() => {
  mounted?.unmount()
  vi.clearAllMocks()
})

const TRACKERS = [{ id: 3, display_name: "Kinozal.me", mirrors: ["kinozal.me"] },
                  { id: 5, display_name: "RuTracker.org", mirrors: ["rutracker.org"] }]
const READERS = [{ name: "Kinozal.me", kind: "tracker", domains: ["kinozal.me", "kinozal.guru"] },
                 { name: "maccms", kind: "site", domains: ["jszyapi.com", "lziapi.com"] }]

async function opened() {
  client.GET.mockImplementation(async (path: string) => path === "/api/trackers" ? { data: TRACKERS } : { data: { readers: READERS } })
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

describe("the tab «Источники»", () => {
  it("is one row choosing a tracker; the mirrors of the chosen one lie under it, each change kept at once", async () => {
    const w = await opened()
    expect(w.find(".sources-editor .mirror-pill").exists()).toBe(false)
    await choose(w, "RuTracker.org")
    expect(w.findAll(".sources-editor .mirror-pill").map((pill) => pill.text())).toEqual(["rutracker.org"])

    await w.find(".sources-editor input").setValue("https://rutracker.net/forum")
    await w.find(".sources-editor .btn-add").trigger("click")
    await flushPromises()
    expect(client.PUT).toHaveBeenLastCalledWith("/api/trackers/{tracker_id}",
      { params: { path: { tracker_id: 5 } }, body: { mirrors: ["rutracker.org", "rutracker.net"] } })
    expect(w.findAll(".sources-editor .mirror-pill").map((pill) => pill.text())).toEqual(["rutracker.org", "rutracker.net"])

    await w.find(".sources-editor .mirror-pill-remove").trigger("click")
    await flushPromises()
    expect(client.PUT).toHaveBeenLastCalledWith("/api/trackers/{tracker_id}",
      { params: { path: { tracker_id: 5 } }, body: { mirrors: ["rutracker.net"] } })
    await choose(w, "Kinozal.me")
    expect(w.findAll(".sources-editor .mirror-pill").map((pill) => pill.text())).toEqual(["kinozal.me"])
  })

  it("shows what the core holds when it refuses a change", async () => {
    const w = await opened()
    await choose(w, "Kinozal.me")
    client.PUT.mockImplementation(async () => ({ error: { error: "refused" } }))
    await w.find(".sources-editor .mirror-pill-remove").trigger("click")
    await flushPromises()
    expect(w.findAll(".sources-editor .mirror-pill").map((pill) => pill.text())).toEqual(["kinozal.me"])
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
