// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { mount, type VueWrapper } from "@vue/test-utils"
import ToastService from "primevue/toastservice"
import { createPinia, setActivePinia } from "pinia"
import { nextTick } from "vue"
import SettingsAgents from "./SettingsAgents.vue"
import { useQueuesStore, type PackCard } from "../../stores/queues"
import { t } from "../../i18n"

const stages = (states: string[]) => ["queue", "build", "verify", "apply"].map((name, index) =>
  ({ name, state: states[index], seconds: 12 })) as PackCard["stages"]
const task = (over: Partial<PackCard> = {}): PackCard => ({
  id: "1:s01e704", series_id: 1, series_name: "Боевой мастер", sync_started: "2026-11-10T09:17:00+00:00", episode: "s01e704",
  name: "e704.mkv", video: { source_id: 7, source_n: 1, source_color: 0, place: "1.ruchime.org", player: "vk", player_label: "VK", height: 1080 },
  status: "build", stages: stages(["done", "active", "pending", "pending"]),
  tracks: [{ number: 1, label: "ПВА ШОУ", kind: "own" }, { number: 2, label: "ruchime", kind: "kept" },
           { number: 3, label: "AniStar", kind: "new" }, { number: 4, label: "Old", kind: "gone" }],
  reason: "", waited: false, figures: { progress: 54, written_mb: 812, speed_x: 103, eta: 12 },
  queued_at: "2026-11-10T09:18:00+00:00", finished_at: "", seconds: 61, ...over,
} as PackCard)

let mounted: VueWrapper | null = null
const section = () => {
  mounted = mount(SettingsAgents, { global: { plugins: [ToastService], stubs: { ConformQueue: true } } })
  return mounted.findAll(".queue-section").find((each) => each.text().includes(t("agents.pack.title")))!
}

describe("the queue of the packing in the window of agents", () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => mounted?.unmount())

  it("says that no packing goes on while the queue is empty", () => {
    expect(section().text()).toContain(t("agents.pack.empty"))
  })

  it("shows each container with its stages, its progress and its tracks as numbers of the season with what changed", async () => {
    const queues = useQueuesStore()
    const found = section()
    queues.setPack([task({ status: "queued", stages: stages(["active", "pending", "pending", "pending"]), figures: {} }), task()])
    await nextTick()

    const cards = found.findAll(".pack-card")
    expect(cards).toHaveLength(2)
    expect(cards[0].text()).toContain(t("pack.status.queued"))
    expect(cards[1].classes()).toContain("is-running")
    expect(cards[1].text()).toContain("s01e704")
    expect(cards[1].text()).toContain("e704.mkv")
    expect(cards[1].find(".progress-bar").text()).toBe("54%")
    expect(cards[1].findAll(".work-tracks .figure-mark").map((each) => [each.text(), each.classes().at(-1)]))
      .toEqual([["1", "is-own"], ["2", "is-kept"], ["3", "is-new"], ["4", "is-gone"]])
    expect(cards[1].find(".pack-added").text()).toContain("AniStar")
    expect(cards[1].find(".pack-gone").text()).toContain("Old")
  })

  it("a built container waits for the journal in no stage and says how long and why", async () => {
    const queues = useQueuesStore()
    const found = section()
    queues.setPack([task({ status: "built", stages: stages(["done", "done", "done", "pending"]), figures: { wait_s: 145 } })])
    await nextTick()
    const card = found.find(".pack-card")
    expect(card.text()).toContain(t("pack.status.built"))
    expect(card.find(".pack-waits").text()).toContain(t("pack.value.waits", { time: "2:25" }))
    expect(card.findAll(".work-step.is-active")).toHaveLength(0)
    expect(card.find(".progress-bar").exists()).toBe(false)
  })

  it("follows every packet it is pushed, with no question of its own", async () => {
    const queues = useQueuesStore()
    const found = section()
    queues.setPack([task({ status: "failed", waited: true })])
    await nextTick()
    expect(found.text()).toContain(t("pack.status.failed"))
    expect(found.text()).toContain(t("pack.outcome.waited"))
    queues.setPack([])
    await nextTick()
    expect(found.text()).toContain(t("agents.pack.empty"))
  })

  it("shows the journal of a sync step by step with the file under way, whichever episode's it is", async () => {
    const queues = useQueuesStore()
    const found = section()
    queues.setJournals([{ series_id: 40, series_name: "Владыка множества миров", sync_started: "2026-11-11T02:44:35+00:00",
                          kind: "sync", steps_of: 507, steps_done: 230, step: "retag",
                          file: "Ten Thousand Worlds s01e394 [ruchime] VK-DL 2160p.mkv.off",
                          started_at: "2026-11-11T02:49:13+00:00", seconds: 341 }])
    await nextTick()
    const journal = found.find(".pack-journal")
    expect(journal.text()).toContain(t("pack.journal.steps", { done: 230, of: 507 }))
    expect(journal.text()).toContain(`${t("pack.journal.step.retag")}: Ten Thousand Worlds s01e394`)
    expect(journal.text()).toContain(t("pack.journal.kind.sync"))
    expect(found.text()).not.toContain(t("agents.pack.empty"))
    queues.setJournals([])
    await nextTick()
    expect(found.find(".pack-journal").exists()).toBe(false)
  })
})
