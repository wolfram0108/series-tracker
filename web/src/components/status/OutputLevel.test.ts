// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { mount } from "@vue/test-utils"
import { h } from "vue"
import OutputLevel from "./OutputLevel.vue"
import OutputLevers from "./OutputLevers.vue"
import { t } from "../../i18n"
import { groupTarget, type EpisodeView, type Lever, type Row } from "../../lib/output"
import { SPECS } from "../../lib/output.fixtures"

// A playable line of one episode of season 2; each test changes only the fields it is about.
const rowOf = (over: Partial<Row>): Row => ({
  series_id: 1, source_id: 7, row_key: "a", excluded: false, season: 2, episode_start: 1, episode_end: 1,
  voice_label: "Ruchime", voice_group: "Ruchime", voice_name: "Ruchime", player: "vk", player_label: "VK", group: "", text: "", link: "", video: "",
  state: "playable", heights: [1080], ...over,
})

const viewOf = (episode: number, over: Partial<Row>): EpisodeView => ({
  season: 2, episode, references: [], levers: [], dubs: [],
  rows: [rowOf({ episode_start: episode, episode_end: episode, ...over })],
})

// Two episodes of one season, each with a voice-over of its own: the levers of the level offer both.
const VIEWS: EpisodeView[] = [
  viewOf(1, { row_key: "a" }),
  viewOf(2, { row_key: "b", voice_label: "Студия", source_id: 9, player: "kodik", player_label: "Kodik", heights: [720] }),
]

const leverOf = (over: Partial<Lever>): Lever => ({ lever: "voice.take", target: null, value: null, level: "default", ...over })

interface Mounting {
  scope?: "series" | "season"
  title?: string
  season?: number
  levers?: Lever[]
  busy?: boolean
}

const mountLevel = (over: Mounting = {}) => mount(OutputLevel, {
  props: { scope: "series" as const, title: "Series", views: VIEWS, levers: [], specs: SPECS, ...over },
  slots: { default: () => h("div", { class: "episode-stub" }, "episode") },
})
type Mounted = ReturnType<typeof mountLevel>

// The voice-over groups follow the locale, so a group is found by its name.
const takeBox = (w: Mounted, group: string) => {
  const column = w.findAll(".voice-column").find((each) => each.attributes("data-group") === group)
  if (!column) throw new Error(`no group ${group} in the levers`)
  return column.find(".voice-head input")
}

// An episode of season 2 that the user switched off: every slot of it carries the mark off.
const offView = (episode: number): EpisodeView => ({
  ...viewOf(episode, { row_key: `o${episode}` }),
  references: [{ season: 2, episode, part: null, mark: "off", source_id: null, row_key: null, player: null, voice_group: null, height: null, reason: null }],
})

describe("OutputLevel", () => {
  it("offers to switch a season off, and on once every episode of it is off, and says which season", async () => {
    const w = mountLevel({ scope: "season", season: 2 })
    const button = () => w.find(".level-switch")
    expect(button().text()).toBe(t("output.season.disable"))
    await button().trigger("click")
    expect(w.emitted("switch")).toEqual([[2, true]])
    await w.setProps({ views: [offView(1), offView(2)] })
    expect(button().text()).toBe(t("output.season.enable"))
    await button().trigger("click")
    expect(w.emitted("switch")![1]).toEqual([2, false])
    await w.setProps({ views: [offView(1), VIEWS[1]] })
    expect(button().text()).toBe(t("output.season.disable"))
  })

  it("does not offer to switch the series level, and waits while a change is on its way", async () => {
    expect(mountLevel({ scope: "series" }).find(".level-switch").exists()).toBe(false)
    const busy = mountLevel({ scope: "season", season: 2, busy: true })
    expect(busy.find(".level-switch").attributes("disabled")).toBeDefined()
  })

  it("shows its title, the number of its episodes, and its season as data of the root", () => {
    const w = mountLevel({ scope: "season", season: 2, title: "Season 2" })
    expect(w.find(".level-title").text()).toBe("Season 2")
    expect(w.find(".level-count").text()).toBe(t("output.episodes", { n: 2 }))
    expect(w.attributes("data-scope")).toBe("season")
    expect(w.attributes("data-season")).toBe("2")
  })

  it("names no season for the series level", () => {
    const w = mountLevel({ scope: "series" })
    expect(w.attributes("data-scope")).toBe("series")
    expect(w.attributes("data-season")).toBeUndefined()
  })

  it("shows its episodes until they are folded, and brings them back on unfold", async () => {
    const w = mountLevel()
    const fold = w.find(".level-fold")
    expect(w.find(".episode-stub").exists()).toBe(true)
    expect(fold.text()).toBe(t("output.fold"))
    expect(fold.attributes("aria-expanded")).toBe("true")
    await fold.trigger("click")
    expect(w.find(".episode-stub").exists()).toBe(false)
    expect(fold.text()).toBe(t("output.unfold"))
    expect(fold.attributes("aria-expanded")).toBe("false")
    await fold.trigger("click")
    expect(w.find(".episode-stub").exists()).toBe(true)
  })

  it("opens its levers by the button and names the action on it", async () => {
    const w = mountLevel()
    const button = w.find(".level-levers")
    expect(w.findComponent(OutputLevers).exists()).toBe(false)
    expect(button.text()).toBe(t("output.levers.show"))
    await button.trigger("click")
    expect(w.findComponent(OutputLevers).exists()).toBe(true)
    expect(button.text()).toBe(t("output.levers.hide"))
    expect(button.attributes("aria-expanded")).toBe("true")
  })

  it("offers in its levers the choices of all its episodes", async () => {
    const w = mountLevel()
    await w.find(".level-levers").trigger("click")
    expect(w.findAll(".variant-reference")).toHaveLength(2)
  })

  it("sends a set of the series level with an empty scope", async () => {
    const w = mountLevel()
    await w.find(".level-levers").trigger("click")
    await takeBox(w, "Ruchime").setValue(false)
    expect(w.emitted("set")).toEqual([[{}, "voice.take", groupTarget("Ruchime"), false]])
  })

  it("sends a set of the season level with the number of the season as its scope", async () => {
    const w = mountLevel({ scope: "season", season: 2, title: "Season 2" })
    await w.find(".level-levers").trigger("click")
    await takeBox(w, "Ruchime").setValue(false)
    expect(w.emitted("set")).toEqual([[{ season: 2 }, "voice.take", groupTarget("Ruchime"), false]])
  })

  it("sends a reset of the season level with the number of the season as its scope", async () => {
    const w = mountLevel({
      scope: "season", season: 2, title: "Season 2",
      levers: [leverOf({ target: groupTarget("Ruchime"), value: false, level: "season" })],
    })
    await w.find(".level-levers").trigger("click")
    await w.find('.voice-column[data-group="Ruchime"] .voice-head .lever-reset').trigger("click")
    expect(w.emitted("reset")).toEqual([[{ season: 2 }, "voice.take", groupTarget("Ruchime")]])
  })
})
