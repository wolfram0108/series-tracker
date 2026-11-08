// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { t } from "../../../i18n"
import type { components } from "../../../api/schema"
import { refVariants, trackCells, widestFitting, type EpisodeView, type TrackCell } from "../../../lib/output"
import type { VoiceDub } from "../../../lib/plan"
import SourceMark from "./SourceMark.vue"
import TrackStrip from "./TrackStrip.vue"
import EpisodeCard from "./EpisodeCard.vue"

type Reference = components["schemas"]["Reference"]

// Episode 1, its reference the single video of source 7 (Vibix, 720p); cut out of compilation A of source 8 (Kodik, 2160p)
// when `cut` is given.
const viewOf = (cut: boolean): EpisodeView => ({
  season: 1, episode: 1, rows: [], levers: [], dubs: [], files: [{ unique_id: "u", source_id: 7, row_key: "s1", status: "completed" }],
  labels: { "7:vibix": "Vibix", "8:kodik": "Kodik" },
  references: [{
    season: 1, episode: 1, part: null, mark: "by_level", source_id: 7, row_key: "s1", player: "vibix", voice_group: "Ruchime",
    height: 720, reason: null,
    compilation: cut ? { key: "k", source_id: 8, row_key: "c", player: "kodik", voice_group: "Ruchime", height: 2160, start: 1, end: 3 } : null,
  } as Reference],
})
const cells: TrackCell[] = [{ pos: 1, group: "Ruchime", state: "done", isReference: true }]
const marks = { 7: { n: 1, color: 0, place: "ruchime.org" }, 8: { n: 2, color: 1, place: "vkvideo.ru @mir" } }
const badge = { key: "k", letter: "A", color: 3 }
const words = (parts: ReturnType<typeof refVariants>[number]) =>
  parts.map((part) => part.kind === "mark" ? `[${part.sourceId}]` : part.kind === "letter" ? "[A]"
    : part.kind === "height" ? `${part.height}p` : part.text).join(" ")

describe("the line of where the picture of an episode is from", () => {
  it("for a cut episode grows from the source, the compilation and the height of the picture to the players and the places", () => {
    expect(refVariants(viewOf(true), marks).map(words)).toEqual([
      "[7] [A] 2160p",
      "[7] 720p [A] 2160p",
      "[7] Vibix 720p [A] Kodik 2160p",
      "[7] ruchime.org Vibix 720p [A] vkvideo.ru @mir Kodik 2160p",
    ])
  })

  it("for an episode that is not cut grows from its source and height to its player and its place", () => {
    expect(refVariants(viewOf(false), marks).map(words)).toEqual(["[7] 720p", "[7] Vibix 720p", "[7] ruchime.org Vibix 720p"])
  })

  it("takes the most detailed variant that fits the room, and the first when none does", () => {
    expect(widestFitting([40, 80, 120, 200], 130)).toBe(2)
    expect(widestFitting([40, 80, 120, 200], 300)).toBe(3)
    expect(widestFitting([40, 80, 120, 200], 10)).toBe(0)
  })

  it("is shown on the card with the letter of the compilation in its colour, and no letter in the corner", async () => {
    const w = mount(EpisodeCard, { props: { view: viewOf(true), cells, status: "ready", marks, badge } })
    await flushPromises()  // the card has measured its room (jsdom: no width, every variant fits) and drawn the line again
    const line = w.find(".card-ref-shown")
    expect(line.find(".ref-letter").text()).toBe("A")
    expect(line.findAll(".ref-text").map((each) => each.text()))
      .toEqual(["ruchime.org", "Vibix", t("output.height", { height: 720 }), "vkvideo.ru @mir", "Kodik", t("output.height", { height: 2160 })])
    expect(w.find(".card-badge").exists()).toBe(false)
  })

  it("keeps the letter in the corner for an episode of a compilation that is not cut", () => {
    const w = mount(EpisodeCard, { props: { view: viewOf(false), cells, status: "ready", marks, badge } })
    expect(w.find(".card-ref-shown .ref-letter").exists()).toBe(false)
    expect(w.find(".card-badge").text()).toBe("A")
  })
})

describe("the corner of presence on the marks", () => {
  // The file of the episode keeps a video its source gives (`fileOnline`); the layout has the single row of the reference
  // or has not (`single`).
  const presenceView = (cut: boolean, fileOnline: boolean, single: boolean): EpisodeView => ({
    ...viewOf(cut),
    files: [{ unique_id: "u", source_id: 7, row_key: "s1", status: "completed", online: fileOnline }],
    rows: single ? [{ source_id: 7, row_key: "s1" } as EpisodeView["rows"][number]] : [],
  })
  const presenceOf = (parts: ReturnType<typeof refVariants>[number]) =>
    parts.filter((part) => part.kind === "mark" || part.kind === "letter").map((part) => [part.kind, part.online])

  it("tells of the video of the file on the mark of the reference of an episode that is not cut", () => {
    expect(presenceOf(refVariants(presenceView(false, false, true), marks)[0])).toEqual([["mark", false]])
    expect(presenceOf(refVariants(presenceView(false, true, false), marks)[0])).toEqual([["mark", true]])
  })

  it("tells of the compilation on its letter and of the single video it was cut by on the mark of a cut episode", () => {
    expect(presenceOf(refVariants(presenceView(true, true, false), marks)[0])).toEqual([["mark", false], ["letter", true]])
    expect(presenceOf(refVariants(presenceView(true, false, true), marks)[0])).toEqual([["mark", true], ["letter", false]])
  })

  it("gives a cell of a track of the file its presence, and none to a voice-over the file has not", () => {
    const dub = (voice: string, state: VoiceDub["state"], online: boolean | null): VoiceDub =>
      ({ season: 1, episode: 1, part: null, voice_key: voice, voice_label: voice, state, source_id: 9, player: null, online })
    const view = { ...presenceView(false, false, true), dubs: [dub("B", "done", true), dub("C", "absent", null), dub("D", "done", false)] }
    expect(trackCells(view, ["Ruchime", "B", "C", "D"]).map((cell) => [cell.group, cell.online]))
      .toEqual([["Ruchime", false], ["B", true], ["C", null], ["D", false]])
  })

  it("draws the corner on a mark and on a cell, and says it in the title", () => {
    const mark = mount(SourceMark, { props: { mark: marks[7], online: false } })
    expect(mark.find(".figure-mark").classes()).toEqual(expect.arrayContaining(["presence", "is-offline"]))
    expect(mark.find(".figure-mark").attributes("title")).toContain(t("output.presence.offline"))
    expect(mount(SourceMark, { props: { mark: marks[7] } }).find(".presence").exists()).toBe(false)
    const strip = mount(TrackStrip, { props: { cells: [{ ...cells[0], online: true }, { pos: 2, group: "C", state: "absent", isReference: false, online: null }] } })
    expect(strip.findAll(".track-cell").map((cell) => cell.classes().filter((name) => ["presence", "is-online", "is-offline"].includes(name))))
      .toEqual([["presence", "is-online"], []])
  })
})

