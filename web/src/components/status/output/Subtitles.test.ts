// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { mount } from "@vue/test-utils"
import { t } from "../../../i18n"
import { namesGroup, orderValue, queueLevers, type EpisodeView, type QueueRow } from "../../../lib/output"
import { planSubtitles, type PlannerView, type SubtitleLine } from "../../../lib/plan"
import QueuePanel from "./QueuePanel.vue"
import TrackStrip from "./TrackStrip.vue"
import TrackTable from "./TrackTable.vue"

// The subtitles of the output (stage 15, DESIGN.md 7.1.1): one table on the series and one on an episode, tracks of the file
// in the order of the table, after the sounds.

const variant = (group: string, source_id: number, language: string, subtitle_type: string, allowed = true): QueueRow => ({
  group, source_id, player: `torrent:${source_id}`, player_label: "", episodes: "1–12", stale: "", allowed,
  allowed_level: allowed ? "default" : "series", language, subtitle_type, default: false, forced: false })
const marks = { 1: { n: 1, color: 0, place: "kinozal.tv · 2144110" }, 2: { n: 2, color: 1, place: "rutracker.org · 6612345" } }

describe("the table of the subtitles of a level", () => {
  it("lists the name, the language and the type of each variant, with no player, height or mark of a first one", () => {
    const w = mount(QueuePanel, { props: { queue: "subtitles", level: "series", marks,
      view: { order_level: "default", same_level: "default",
        rows: [variant("Полные", 1, "rus", "full"), variant("Signs", 2, "eng", "signs", false)] } } })
    expect(w.text()).toContain(t("output.queue.hintSubtitles"))
    const heads = w.findAll("th").map((each) => each.text())
    expect(heads).toContain(t("output.subtitles.name"))
    expect(heads).not.toContain(t("output.queue.player"))
    expect(heads).not.toContain(t("output.queue.height"))
    const rows = w.findAll(".variant-row")
    expect(rows[0].find(".subtitle-name").text()).toBe("Полные")
    expect(rows[0].text()).toContain(t("output.subtitles.kind.full"))
    expect(rows[0].find(".pill").exists()).toBe(false) // every row taken is a track: none stands first
    expect(rows[1].find(".pill").text()).toBe(t("output.queue.skipped"))
    expect(w.find(".same-pick").exists()).toBe(false)
  })

  it("shows how the core lays the tracks in the container, with nothing to set there", () => {
    const shown = [{ ...variant("Полные", 1, "rus", "full"), default: true }, { ...variant("Надписи", 1, "rus", "signs"), forced: true },
      variant("Off", 2, "eng", "full", false)]
    const w = mount(QueuePanel, { props: { queue: "subtitles", level: "series", marks,
      view: { order_level: "default", same_level: "default", rows: shown } } })
    const heads = w.findAll("th").map((each) => each.text())
    expect(heads).toEqual(expect.arrayContaining([t("output.subtitles.default"), t("output.subtitles.forced")]))
    const rows = w.findAll(".variant-row")
    expect(rows.map((row) => [row.find(".subtitle-default").exists(), row.find(".subtitle-forced").exists()]))
      .toEqual([[true, false], [false, true], [false, false]])
    expect(w.findAll(".queue-flag input").length).toBe(0)
  })

  it("says it is the table of the episode on an episode", () => {
    const w = mount(QueuePanel, { props: { queue: "subtitles", level: "episode", marks,
      view: { order_level: "episode", same_level: "default", rows: [variant("Полные", 1, "rus", "full")] } } })
    expect(w.text()).toContain(t("output.queue.hintSubtitlesEpisode"))
    expect(w.find(".queue-back").exists()).toBe(true)
  })

  it("is set by the levers of the queue of the subtitles, an order naming the name of each variant", () => {
    const specs = [
      { name: "subtitles.order", target: "none", shape: "variant_order", default: [], consumer: "planner", queue: "subtitles" },
      { name: "subtitles.allow", target: "variant", shape: "flag", default: true, consumer: "planner", queue: "subtitles" },
    ] as const
    expect(queueLevers([...specs], "subtitles")).toEqual({ order: "subtitles.order", allow: "subtitles.allow", take: undefined, same: undefined })
    expect(namesGroup("subtitles")).toBe(true)
    expect(orderValue("subtitles", [variant("Надписи", 1, "rus", "signs")])).toEqual([{ group: "Надписи", source_id: 1, player: "torrent:1" }])
  })
})

const plannerView = {
  series_id: 1, running: false, failures: [], commands: [],
  assets: [{ name: "s01e01.mkv", off: false, episode: [1, 1], video: { row: { source_id: 1, row_key: "v", video: "" }, player: "torrent:1" },
    tracks: [], subtitles: [{ row: { source_id: 1, row_key: "s0", video: "" }, name: "Полные", player: "torrent:1" }] }],
  episodes: [
    { slot: [1, 1], name: "s01e01.mkv", video: { kind: "asset", row: { source_id: 1, row_key: "v", video: "" } }, tracks: [],
      subtitles: [
        { row: { source_id: 1, row_key: "s0", video: "" }, name: "Полные", player: "torrent:1", language: "rus", subtitle_type: "full", kind: "reuse" },
        { row: { source_id: 1, row_key: "s1", video: "" }, name: "Надписи", player: "torrent:1", language: "rus", subtitle_type: "signs", kind: "take" }],
      unaligned: [{ group: "Full", source_id: 2, player: "torrent:2" }] },
    { slot: [1, 2], name: "s01e02.mkv", video: { kind: "download", row: { source_id: 1, row_key: "w", video: "" } }, tracks: [],
      subtitles: [{ row: { source_id: 1, row_key: "t0", video: "" }, name: "Полные", player: "torrent:1", language: "rus", subtitle_type: "full", kind: "take" }],
      unaligned: [] }],
} as unknown as PlannerView

describe("the subtitles of the files", () => {
  it("are those of the plan in its order, held or planned, then those that wait for their alignment", () => {
    expect(planSubtitles(plannerView, 1, 1).map((each) => [each.n, each.name, each.state])).toEqual([
      [1, "Полные", "done"], [2, "Надписи", "planned"], [null, "Full", "unaligned"]])
  })

  it("follow the sounds in the table of the tracks, «Удалить» leaving the variant out of the episode", async () => {
    const view = { season: 1, episode: 1, references: [], rows: [], levers: [], dubs: [] } as EpisodeView
    const w = mount(TrackTable, { props: { view, cells: [], marks, subtitles: planSubtitles(plannerView, 1, 1) } })
    const rows = w.findAll(".subtitle-row")
    expect(rows.map((row) => row.find(".subtitle-mark").text())).toEqual(["С1", "С2", "—"])
    expect(rows[2].text()).toContain(t("output.subtitles.unaligned"))
    expect(rows[2].find(".do-drop-subtitles").exists()).toBe(false)
    await rows[0].find(".do-drop-subtitles").trigger("click")
    expect(w.emitted("drop-subtitles")?.[0]?.[0]).toMatchObject({ group: "Полные", source_id: 1, player: "torrent:1" })
  })

  it("are drawn on the card after the sounds, filled when the file holds them", () => {
    const lines: SubtitleLine[] = planSubtitles(plannerView, 1, 1)
    const w = mount(TrackStrip, { props: { cells: [], subtitles: lines } })
    const cells = w.findAll(".subtitle-cell")
    expect(cells.map((each) => [each.text(), each.classes("is-held")])).toEqual([["С1", true], ["С2", false]])
  })
})
