// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { mount, type VueWrapper } from "@vue/test-utils"
import ToastService from "primevue/toastservice"
import { createPinia, setActivePinia } from "pinia"
import { nextTick } from "vue"
import SettingsAgents from "./SettingsAgents.vue"
import { useQueuesStore, type PackTask } from "../../stores/queues"
import { t } from "../../i18n"

const task = (over: Partial<PackTask> = {}): PackTask => ({
  id: "pack:1:1:1:null:", series_id: 1, series_name: "Боевой мастер", season: 1, episode: 704, part: null,
  name: "e704.mkv", status: "building", tracks: ["ПВА ШОУ", "ruchime"], ...over,
})

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

  it("shows each task with its series, its episode, its status, its file and its tracks in order", async () => {
    const queues = useQueuesStore()
    const found = section()
    queues.setPack([task({ status: "queued", episode: null, season: null, name: "", tracks: [] }), task()])
    await nextTick()

    const cards = found.findAll(".card-queue")
    expect(cards).toHaveLength(2)
    expect(cards[0].text()).toContain(t("agents.pack.series"))
    expect(cards[0].text()).toContain(t("agents.pack.status.queued"))
    expect(cards[1].text()).toContain(t("agents.pack.episode", { season: 1, episode: 704 }))
    expect(cards[1].text()).toContain(t("agents.pack.status.building"))
    expect(cards[1].text()).toContain("e704.mkv")
    expect(cards[1].text()).toMatch(/1\. ПВА ШОУ[\s\S]*2\. ruchime/)
  })

  it("follows every packet it is pushed, with no question of its own", async () => {
    const queues = useQueuesStore()
    const found = section()
    queues.setPack([task({ status: "failed" })])
    await nextTick()
    expect(found.text()).toContain(t("agents.pack.status.failed"))
    queues.setPack([])
    await nextTick()
    expect(found.text()).toContain(t("agents.pack.empty"))
  })
})
