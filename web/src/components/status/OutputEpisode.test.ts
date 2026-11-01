// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { mount } from "@vue/test-utils"
import OutputEpisode from "./OutputEpisode.vue"
import OutputLevers from "./OutputLevers.vue"
import { t } from "../../i18n"
import { groupTarget, type EpisodeView, type Lever, type Reference, type Row } from "../../lib/output"
import { SPECS } from "../../lib/output.fixtures"
import type { VoiceDub } from "../../stores/voices"

// A playable single-episode line of the VK player; each test changes only the fields it is about.
const rowOf = (over: Partial<Row>): Row => ({
  series_id: 1, source_id: 7, row_key: "a", excluded: false, season: 1, episode_start: 5, episode_end: 5,
  voice_label: "Ruchime", voice_name: "Ruchime", player: "vk", player_label: "VK", group: "", text: "", link: "",
  state: "playable", heights: [1080], ...over,
})

// The answer of the episode for one part: by default, the Ruchime line of the VK player at 1080p.
const refOf = (over: Partial<Reference>): Reference => ({
  season: 1, episode: 5, part: null, mark: "by_level", source_id: 7, row_key: "a", player: "vk",
  voice_group: "Ruchime", height: 1080, reason: null, ...over,
})

const dubOf = (over: Partial<VoiceDub>): VoiceDub => ({
  season: 1, episode: 5, voice_key: "ruchime", voice_label: "Ruchime", status: "placed", state: "done",
  hint: null, detail: null, ...over,
})

const leverOf = (over: Partial<Lever>): Lever => ({ lever: "voice.take", target: null, value: null, level: "default", ...over })

// A reason as the core sends it: a code, without facts.
const reason = (code: string): string => JSON.stringify({ code })

const viewOf = (over: Partial<EpisodeView> = {}): EpisodeView => ({
  season: 1, episode: 5, references: [], rows: [rowOf({})], levers: [], dubs: [], ...over,
})

const mountEpisode = (over: Partial<EpisodeView> = {}, busy = false) =>
  mount(OutputEpisode, { props: { view: viewOf(over), specs: SPECS, busy } })

describe("OutputEpisode file names", () => {
  it("shows the names the files of the episode get when it is given them, and nothing when it is not", () => {
    const named = mount(OutputEpisode, { props: { view: viewOf({ references: [refOf({})] }), specs: SPECS, fileNames: ["Show s01e05.mp4", "Show s01e05 part 2.mp4"] } })
    expect(named.findAll(".episode-file").map((each) => each.text())).toEqual([
      t("output.file", { name: "Show s01e05.mp4" }), t("output.file", { name: "Show s01e05 part 2.mp4" })])
    expect(mountEpisode({ references: [refOf({})] }).find(".episode-files").exists()).toBe(false)
  })
})

describe("OutputEpisode switch", () => {
  const offRef = (): Reference => refOf({ mark: "off", source_id: null, row_key: null, player: null, voice_group: null, height: null })

  it("offers to switch the episode off, says which one, and is no departure once it is off", async () => {
    const w = mountEpisode({ references: [refOf({})] })
    expect(w.find(".episode-switch").text()).toBe(t("output.episode.disable"))
    await w.find(".episode-switch").trigger("click")
    expect(w.emitted("switch")).toEqual([[1, 5, true]])
    const off = mountEpisode({ references: [offRef()] })
    expect(off.classes()).toContain("is-off")
    expect(off.classes()).not.toContain("is-deviation")
    expect(off.find(".episode-marks").text()).toContain(t("output.mark.off"))
    expect(off.find(".episode-switch").text()).toBe(t("output.episode.enable"))
    await off.find(".episode-switch").trigger("click")
    expect(off.emitted("switch")).toEqual([[1, 5, false]])
  })

  it("waits while a change is on its way", () => {
    expect(mountEpisode({ references: [refOf({})] }, true).find(".episode-switch").attributes("disabled")).toBeDefined()
  })
})

describe("OutputEpisode", () => {
  it("names the episode and its reference by group, player and height, and carries its season and number", () => {
    const w = mountEpisode({ references: [refOf({})] })
    expect(w.attributes("data-season")).toBe("1")
    expect(w.attributes("data-episode")).toBe("5")
    expect(w.find("header").text()).toContain(t("output.episode", { n: 5 }))
    expect(w.find("header").text()).toContain("Ruchime — VK 1080p")
  })

  it("names a reference with no group by the catalog and one with no height without the height", () => {
    const w = mountEpisode({ references: [refOf({ voice_group: "", height: null })] })
    expect(w.find("header").text()).toContain(
      t("output.lever.referenceLabel", { group: t("output.lever.noGroup"), player: "VK" }),
    )
  })

  it("names a reference by the part of the episode it is", () => {
    const w = mountEpisode({ references: [refOf({ part: 2 })] })
    expect(w.find("header").text()).toContain(t("output.part", { n: 2 }))
  })

  it("keeps the mark of a reference with no source and names no player for it", () => {
    const w = mountEpisode({
      references: [refOf({ mark: "unfit", source_id: null, player: null, voice_group: null, height: null })],
    })
    expect(w.find("header").text()).not.toContain("Ruchime")
    expect(w.find("header").text()).not.toContain("VK")
    expect(w.find(".episode-marks").text()).toContain(t("output.mark.unfit"))
  })

  it("shows the mark of each reference and the word of its reason", () => {
    const w = mountEpisode({ references: [refOf({ mark: "unfit", reason: reason("no_source") })] })
    expect(w.find(".episode-marks").text()).toContain(t("output.mark.unfit"))
    expect(w.find(".episode-reason").text()).toBe(t("reason.no_source"))
  })

  it("puts the part before the mark of each part of a composite episode, and none before a whole episode", () => {
    const parts = mountEpisode({ references: [refOf({ part: 1, mark: "own" }), refOf({ part: 2, mark: "none" })] })
    expect(parts.findAll(".episode-marks li").map((li) => li.text())).toEqual([
      `${t("output.part", { n: 1 })}${t("output.mark.own")}`, `${t("output.part", { n: 2 })}${t("output.mark.none")}`,
    ])
    const whole = mountEpisode({ references: [refOf({})] })
    expect(whole.find(".episode-marks li").text()).toBe(t("output.mark.by_level"))
  })

  it("shows the none mark for an episode with lines and no reference", () => {
    expect(mountEpisode({ references: [] }).find(".episode-marks").text()).toBe(t("output.mark.none"))
  })

  it("shows no marks for an episode with no lines and no reference", () => {
    expect(mountEpisode({ rows: [], references: [] }).find(".episode-marks").exists()).toBe(false)
  })

  it("shows a voice-over in error with the word of its state and of its reason, and its state class", () => {
    const w = mountEpisode({
      dubs: [dubOf({
        voice_label: "Студия", voice_key: "studio", status: "failed", state: "error", detail: { code: "align_failed" },
      })],
    })
    const item = w.find(".dub-error")
    expect(item.exists()).toBe(true)
    expect(item.text()).toContain("Студия")
    expect(item.text()).toContain(t("voices.state.error"))
    expect(item.text()).toContain(t("reason.align_failed"))
  })

  it("shows the hint of a voice-over that has one, under the heading of the voice-overs", () => {
    const w = mountEpisode({ dubs: [dubOf({ state: "downloading", status: "downloading", hint: "downloading" })] })
    expect(w.find("h4").text()).toBe(t("output.voices"))
    expect(w.find(".dub-downloading").text()).toContain(t("voices.hint.downloading"))
  })

  it("hides the voice-overs block when the episode has no voice-overs", () => {
    const w = mountEpisode({ dubs: [] })
    expect(w.find(".episode-voices").exists()).toBe(false)
    expect(w.text()).not.toContain(t("output.voices"))
  })

  it("is a deviation when a mark is not by level or the episode has its own lever, and not otherwise", () => {
    expect(mountEpisode({ references: [refOf({ mark: "own" })] }).classes()).toContain("is-deviation")
    expect(mountEpisode({ references: [refOf({ mark: "by_level" })] }).classes()).not.toContain("is-deviation")
    const own = leverOf({ target: groupTarget("Ruchime"), value: false, level: "episode" })
    expect(mountEpisode({ references: [refOf({})], levers: [own] }).classes()).toContain("is-deviation")
  })

  it("opens its levers by the button, names the action on it and folds them again", async () => {
    const w = mountEpisode({ references: [refOf({})] })
    const toggle = w.find(".episode-toggle")
    expect(w.findComponent(OutputLevers).exists()).toBe(false)
    expect(toggle.text()).toBe(t("output.levers.show"))
    expect(toggle.attributes("aria-expanded")).toBe("false")
    await toggle.trigger("click")
    expect(w.findComponent(OutputLevers).props("scope")).toBe("episode")
    expect(toggle.text()).toBe(t("output.levers.hide"))
    expect(toggle.attributes("aria-expanded")).toBe("true")
    await toggle.trigger("click")
    expect(w.findComponent(OutputLevers).exists()).toBe(false)
  })

  it("sends a set of its levers up with the scope of the episode", async () => {
    const w = mountEpisode({})
    await w.find(".episode-toggle").trigger("click")
    await w.find('.voice-column[data-group="Ruchime"] .voice-head input').setValue(false)
    expect(w.emitted("set")).toEqual([[{ season: 1, episode: 5 }, "voice.take", groupTarget("Ruchime"), false]])
  })

  it("sends a reset of its levers up with the scope of the episode", async () => {
    const w = mountEpisode({ levers: [leverOf({ target: groupTarget("Ruchime"), value: false, level: "episode" })] })
    await w.find(".episode-toggle").trigger("click")
    await w.find('.voice-column[data-group="Ruchime"] .voice-head .lever-reset').trigger("click")
    expect(w.emitted("reset")).toEqual([[{ season: 1, episode: 5 }, "voice.take", groupTarget("Ruchime")]])
  })

  it("passes busy to its levers, which then block their controls", async () => {
    const w = mountEpisode({}, true)
    await w.find(".episode-toggle").trigger("click")
    expect((w.find('.voice-column[data-group="Ruchime"] .voice-head input').element as HTMLInputElement).disabled).toBe(true)
  })
})
