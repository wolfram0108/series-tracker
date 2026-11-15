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
      view: { order_level: "default", same_level: "default", rows: [row("cvh"), row("kodik")] } } })
    expect(w.find(".queue-follows").text()).toBe(t("output.queue.followsReference"))
    expect(w.text()).toContain(t("output.queue.hintMarkupFollows"))
    expect(w.find(".queue-back").exists()).toBe(false)
    expect(w.find(".variant-row .pill").text()).toBe(t("output.queue.markup"))
  })

  it("has its own order and the way back to the level above once a level has one", async () => {
    const w = mount(QueuePanel, { props: { queue: "markup", level: "season", marks: {}, follows: false,
      view: { order_level: "season", same_level: "default", rows: [row("cvh"), row("kodik")] } } })
    expect(w.find(".queue-follows").exists()).toBe(false)
    await w.find(".queue-back").trigger("click")
    expect(w.emitted("reset")).toHaveLength(1)
  })
})

describe("what the file holds (the fact) against the order of the queue (the wish)", () => {
  it("marks the variant the file holds whatever its place in the order, beside the mark of the first one taken", () => {
    const w = mount(QueuePanel, { props: { queue: "voice", level: "episode", marks: {}, held: { source_id: 55, player: "kodik" },
      view: { order_level: "default", same_level: "default", rows: [row("cvh"), row("kodik")] } } })
    const rows = w.findAll(".variant-row")
    expect(rows[0].find(".pill").text()).toBe(t("output.queue.taken"))
    expect(rows[0].find(".queue-held").exists()).toBe(false)
    expect(rows[1].find(".queue-held").text()).toBe(t("output.queue.held"))
  })

  it("marks nothing for an episode with no file or a file that does not hold the voice-over", () => {
    for (const held of [null, undefined, { source_id: 55, player: "vk" }]) {
      const w = mount(QueuePanel, { props: { queue: "voice", level: "episode", marks: {}, held,
        view: { order_level: "default", same_level: "default", rows: [row("cvh"), row("kodik")] } } })
      expect(w.find(".queue-held").exists()).toBe(false)
    }
  })
})

// Variants the user declared equal (Р-99).
const same = (player: string, number: number | null, source = 55) => ({ ...row(player), source_id: source, same: number })
const view = (rows: ReturnType<typeof same>[], over: object = {}) => ({ order_level: "default" as const, same_level: "default" as const, rows, ...over })

describe("the variants the user declared equal", () => {
  const mountWith = (rows: ReturnType<typeof same>[], props: object = {}, over: object = {}) =>
    mount(QueuePanel, { props: { queue: "reference", level: "episode", marks: {}, view: view(rows, over), ...props } })

  it("stand in rows of one group with its number, the others offer to be picked", () => {
    const w = mountWith([same("a", 1), same("b", 1), same("c", null), same("d", 2), same("e", 2)])
    expect(w.findAll(".same-chip").map((each) => each.text())).toEqual(["1", "1", "2", "2"])
    expect(w.findAll(".variant-row.in-same-tone-0, .variant-row.same-tone-0")).toHaveLength(2)
    expect(w.findAll(".variant-row.same-tone-1")).toHaveLength(2)
    expect(w.findAll(".same-pick")).toHaveLength(1)
    expect(w.find("th.queue-same").attributes("title")).toBe(t("output.queue.sameHint"))
  })

  it("have no column in the queue of a voice-over", () => {
    const w = mountWith([same("a", null), same("b", null)], { queue: "voice" })
    expect(w.find(".queue-same").exists()).toBe(false)
  })

  it("are made one group by the picked rows: the bar counts them and «Объединить» tells the core the whole value", async () => {
    const w = mountWith([same("a", 1), same("b", 1), same("c", null), same("d", null)])
    const picks = w.findAll(".same-pick")
    expect(w.find(".same-pickbar").exists()).toBe(false)
    await picks[0].trigger("click")
    expect(w.find(".same-pickbar").text()).toContain(t("output.queue.samePicked", { count: 1 }))
    expect(w.find(".same-join").attributes("disabled")).toBeDefined()
    await picks[1].trigger("click")
    await w.find(".same-join").trigger("click")
    const at = (player: string) => ({ group: "OnWave", source_id: 55, player })
    // The two picked rows are of no group: they make one of their own, and the group that stands is kept as it is.
    expect(w.emitted("same")).toEqual([[[[at("a"), at("b")], [at("c"), at("d")]]]])
    expect(w.find(".same-pickbar").exists()).toBe(false)
  })

  it("give up a row or the whole group by the menu of the mark of the group", async () => {
    const w = mountWith([same("a", 1), same("b", 1), same("c", 1), same("d", null)])
    await w.findAll(".same-chip")[1].trigger("click")
    const items = [...document.body.querySelectorAll(".same-menu .same-option")] as HTMLElement[]
    expect(items.map((each) => each.textContent)).toEqual([t("output.queue.samePick"), t("output.queue.sameLeave"),
      t("output.queue.sameSplit", { count: 3 })])
    items[1].click()
    await w.vm.$nextTick()
    expect(w.emitted("same")).toEqual([[[[{ group: "OnWave", source_id: 55, player: "a" }, { group: "OnWave", source_id: 55, player: "c" }]]]])
    await w.findAll(".same-chip")[0].trigger("click")
    ;([...document.body.querySelectorAll(".same-menu .same-option")][2] as HTMLElement).click()
    await w.vm.$nextTick()
    expect(w.emitted("same")![1]).toEqual([[]])
    w.unmount()
  })

  it("tell the level they come from, with the way back to the level above where it is the level of the panel", async () => {
    const own = mountWith([same("a", 1), same("b", 1)], { level: "season" }, { same_level: "season", order_level: "default" })
    expect(own.find(".queue-same-level").text()).toBe(t("output.level.season"))
    expect(own.find(".queue-same-level").classes()).toContain("is-own")
    await own.find(".queue-same-back").trigger("click")
    expect(own.emitted("same")).toEqual([[null]])
    const inherited = mountWith([same("a", 1), same("b", 1)], { level: "episode" }, { same_level: "series" })
    expect(inherited.find(".queue-same-level").text()).toBe(t("output.level.series"))
    expect(inherited.find(".queue-same-back").exists()).toBe(false)
    expect(mountWith([same("a", null), same("b", null)]).find(".queue-same-level").exists()).toBe(false)
  })

  it("show first, among the rows of their group, the one the file holds, and the reference stands on the one the plan takes", () => {
    const rows = [same("x", null), same("a", 1), same("b", 1), same("c", 1)]
    const w = mountWith(rows, { held: { source_id: 55, player: "c" }, taken: { source_id: 55, player: "c" } })
    const shown = w.findAll(".variant-row")
    expect(shown.map((each) => each.find(".queue-player").text())).toEqual(["X", "C", "A", "B"])
    expect(shown[1].find(".pill").text()).toBe(t("output.queue.reference"))
    expect(shown[1].find(".queue-held").exists()).toBe(true)
    expect(shown[0].classes()).not.toContain("is-first")
  })

  it("leave the mark of the reference on the first row taken while the plan takes none of them", () => {
    const w = mountWith([same("a", null), same("b", null)], { taken: { source_id: 99, player: "none" } })
    expect(w.findAll(".variant-row")[0].find(".pill").text()).toBe(t("output.queue.reference"))
  })
})

describe("one source and player give a variant for each voice-over: the file and the plan mark one of them", () => {
  const voices = ["AniLiberty", "Студийный Огурчик", "OnWave"].map((group) => ({ ...row("cvh"), group }))

  it("marks «в файле» on the voice-over of the video the file holds and on no other of the same source and player", () => {
    const w = mount(QueuePanel, { props: { queue: "reference", level: "episode", marks: {}, held: { source_id: 55, player: "cvh", group: "OnWave" },
      view: { order_level: "default", same_level: "default", rows: voices } } })
    const rows = w.findAll(".variant-row")
    expect(rows.map((each) => each.find(".queue-held").exists())).toEqual([false, false, true])
  })

  it("stands the reference on the voice-over the plan takes, and nowhere else", () => {
    const w = mount(QueuePanel, { props: { queue: "reference", level: "episode", marks: {}, taken: { source_id: 55, player: "cvh", group: "Студийный Огурчик" },
      view: { order_level: "default", same_level: "default", rows: voices } } })
    expect(w.findAll(".variant-row").map((each) => each.find(".pill-primary").exists())).toEqual([false, true, false])
  })

  it("marks the file's row in the queue of a voice-over by source and player alone: its voice-over is the queue's", () => {
    const w = mount(QueuePanel, { props: { queue: "voice", level: "episode", marks: {}, held: { source_id: 55, player: "cvh" },
      view: { order_level: "default", same_level: "default", rows: [row("cvh"), row("kodik")] } } })
    expect(w.findAll(".variant-row").map((each) => each.find(".queue-held").exists())).toEqual([true, false])
  })
})

describe("the queue of the markup reference says what the plan takes and what the file lies on", () => {
  it("stands «эталон» on the variant the plan takes and «в файле» on the one the file lies on, whatever their places", () => {
    const rows = ["cvh", "kodik", "vk"].map((player) => row(player))
    const w = mount(QueuePanel, { props: { queue: "markup", level: "episode", marks: {}, follows: false,
      held: { source_id: 55, player: "vk" }, taken: { source_id: 55, player: "kodik" },
      view: { order_level: "season", same_level: "default", rows } } })
    const shown = w.findAll(".variant-row")
    expect(shown.map((each) => each.find(".pill-primary").exists())).toEqual([false, true, false])
    expect(shown[1].find(".pill-primary").text()).toBe(t("output.queue.markup"))
    expect(shown.map((each) => each.find(".queue-held").exists())).toEqual([false, false, true])
  })
})

describe("«Снять все»", () => {
  const view = (rows: ReturnType<typeof row>[]) => ({ order_level: "default" as const, same_level: "default" as const, rows })

  it("presses the box of each row the queue takes once, and leaves the rows it does not take", async () => {
    const w = mount(QueuePanel, { props: { queue: "reference", level: "season", marks: {},
      view: view([row("cvh"), { ...row("kodik"), allowed: false }, row("vibix")]) } })
    await w.find(".queue-clear").trigger("click")
    expect(w.emitted("allow")).toEqual([[expect.objectContaining({ player: "cvh" }), false], [expect.objectContaining({ player: "vibix" }), false]])
  })

  it("is off when no row is taken, and absent for a queue without rows", async () => {
    const none = [{ ...row("cvh"), allowed: false }]
    const w = mount(QueuePanel, { props: { queue: "voice", level: "season", marks: {}, view: view(none) } })
    expect((w.find(".queue-clear").element as HTMLButtonElement).disabled).toBe(true)
    await w.setProps({ view: view([]) })
    expect(w.find(".queue-clear").exists()).toBe(false)
  })

  it("is a lever that is never held, as the boxes are", () => {
    const w = mount(QueuePanel, { props: { queue: "reference", level: "season", marks: {}, view: view([row("cvh")]) } })
    expect(w.find(".queue-clear").attributes("data-lever-free")).toBeDefined()
    expect(w.find(".variant-row input[type=checkbox]").attributes("data-lever-free")).toBeDefined()
  })
})
