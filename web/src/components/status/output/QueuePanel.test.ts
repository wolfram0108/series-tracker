// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { mount } from "@vue/test-utils"
import { t } from "../../../i18n"
import QueuePanel from "./QueuePanel.vue"

const row = (player: string) => ({ group: "OnWave", source_id: 55, player, player_label: player.toUpperCase(), height: 1080,
  episodes: "1", allowed: true, allowed_level: "default" as const })

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
