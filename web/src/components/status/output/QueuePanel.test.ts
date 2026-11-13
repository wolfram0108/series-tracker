// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { mount } from "@vue/test-utils"
import { t } from "../../../i18n"
import QueuePanel from "./QueuePanel.vue"

const row = (player: string) => ({ group: "OnWave", source_id: 55, player, player_label: player.toUpperCase(), height: 1080,
  episodes: "1", stale: "", allowed: true, allowed_level: "default" as const })

describe("the queue of the markup reference (stage 14)", () => {
  it("says it follows the reference, with no way back, while no level has an order of its own", () => {
    const w = mount(QueuePanel, { props: { queue: "markup", level: "season", marks: {}, follows: true,
      view: { order_level: "default", rows: [row("cvh"), row("kodik")] } } })
    expect(w.find(".queue-follows").text()).toBe(t("output.queue.followsReference"))
    expect(w.text()).toContain(t("output.queue.hintMarkupFollows"))
    expect(w.find(".queue-back").exists()).toBe(false)
    expect(w.find(".variant-row .pill").text()).toBe(t("output.queue.markup"))
  })

  it("has its own order and the way back to the level above once a level has one", async () => {
    const w = mount(QueuePanel, { props: { queue: "markup", level: "season", marks: {}, follows: false,
      view: { order_level: "season", rows: [row("cvh"), row("kodik")] } } })
    expect(w.find(".queue-follows").exists()).toBe(false)
    await w.find(".queue-back").trigger("click")
    expect(w.emitted("reset")).toHaveLength(1)
  })
})

describe("what the file holds (the fact) against the order of the queue (the wish)", () => {
  it("marks the variant the file holds whatever its place in the order, beside the mark of the first one taken", () => {
    const w = mount(QueuePanel, { props: { queue: "voice", level: "episode", marks: {}, held: { source_id: 55, player: "kodik" },
      view: { order_level: "default", rows: [row("cvh"), row("kodik")] } } })
    const rows = w.findAll(".variant-row")
    expect(rows[0].find(".pill").text()).toBe(t("output.queue.taken"))
    expect(rows[0].find(".queue-held").exists()).toBe(false)
    expect(rows[1].find(".queue-held").text()).toBe(t("output.queue.held"))
  })

  it("marks nothing for an episode with no file or a file that does not hold the voice-over", () => {
    for (const held of [null, undefined, { source_id: 55, player: "vk" }]) {
      const w = mount(QueuePanel, { props: { queue: "voice", level: "episode", marks: {}, held,
        view: { order_level: "default", rows: [row("cvh"), row("kodik")] } } })
      expect(w.find(".queue-held").exists()).toBe(false)
    }
  })
})
