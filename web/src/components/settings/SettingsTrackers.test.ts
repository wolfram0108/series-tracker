// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils"

const client = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() }))
vi.mock("../../api/client", () => ({ api: client }))
vi.mock("primevue/usetoast", () => ({ useToast: () => ({ add: vi.fn() }) }))

import SettingsTrackers from "./SettingsTrackers.vue"
import { t } from "../../i18n"

let mounted: VueWrapper | null = null
afterEach(() => mounted?.unmount())

describe("the tab «Трекеры»", () => {
  it("ends with every domain the service reads pages of, as the core lists them", async () => {
    client.GET.mockImplementation(async (path: string) => path === "/api/trackers"
      ? { data: [{ id: 3, display_name: "Kinozal.me", mirrors: ["kinozal.me"] }] }
      : { data: { readers: [{ name: "Kinozal.me", kind: "tracker", domains: ["kinozal.me", "kinozal.guru"] },
                            { name: "maccms", kind: "site", domains: ["jszyapi.com", "lziapi.com"] }] } })
    mounted = mount(SettingsTrackers)
    await flushPromises()
    const section = mounted.find(".parsed-domains")
    expect(section.find(".fieldset-header").text()).toBe(t("settings.trackers.domains.title"))
    expect(section.findAll(".domains-row").map((row) => row.findAll(".mirror-pill").map((pill) => pill.text()))).toEqual([
      ["kinozal.me", "kinozal.guru"], ["jszyapi.com", "lziapi.com"]])
    expect(section.findAll(".domains-kind").map((each) => each.text())).toEqual([
      t("settings.trackers.domains.kind.tracker"), t("settings.trackers.domains.kind.site")])
    // Read only: a domain of this list has nothing to remove it by.
    expect(section.find(".mirror-pill-remove").exists()).toBe(false)
  })
})
