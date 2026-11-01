// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { mount, type DOMWrapper } from "@vue/test-utils"
import OutputLevers from "./OutputLevers.vue"
import { t } from "../../i18n"
import { LEVER, groupTarget, playerTarget, type EpisodeView, type Lever, type Row } from "../../lib/output"

// A playable single-episode line; each test changes only the fields it is about.
const rowOf = (over: Partial<Row>): Row => ({
  series_id: 1, source_id: 7, row_key: "a", excluded: false, season: 1, episode_start: 5, episode_end: 5,
  voice_label: "Ruchime", voice_name: "Ruchime", player: "vk", player_label: "VK", group: "", text: "", link: "",
  state: "playable", heights: [1080], ...over,
})

// Source 7 has two players of one group; source 9 has a voice-over of its own and a line without group that is absent.
const ROWS: Row[] = [
  rowOf({ row_key: "a", source_id: 7, player: "vk", player_label: "VK", heights: [720, 1080] }),
  rowOf({ row_key: "b", source_id: 7, player: "vibix", player_label: "Vibix", heights: [1080] }),
  rowOf({ row_key: "c", source_id: 9, player: "kodik", player_label: "", voice_label: "Студия", heights: [720] }),
  rowOf({ row_key: "d", source_id: 9, player: "kodik", player_label: "", voice_label: "", state: "absent", heights: [] }),
]

const viewOf = (rows: Row[] = ROWS): EpisodeView => ({ season: 1, episode: 5, references: [], rows, levers: [], dubs: [] })

const leverOf = (over: Partial<Lever>): Lever => ({ lever: LEVER.take, target: null, value: null, level: "default", ...over })

interface Mounting {
  scope?: "series" | "season" | "episode"
  view?: EpisodeView
  levers?: Lever[]
  busy?: boolean
}

const mountLevers = (over: Mounting = {}) =>
  mount(OutputLevers, { props: { scope: "season" as const, view: viewOf(), levers: [], busy: false, ...over } })
type Mounted = ReturnType<typeof mountLevers>

const CHOSEN = { group: "Ruchime", source_id: 7, player: "vk" }
const selectedOf = (select: DOMWrapper<Element>): string => (select.element as HTMLSelectElement).value
const checkedOf = (box: DOMWrapper<Element>): boolean => (box.element as HTMLInputElement).checked
const valuesOf = (select: DOMWrapper<Element>): string[] =>
  select.findAll("option").map((option) => (option.element as HTMLOptionElement).value)

// The order of voice-over groups follows the locale of the environment, so a group is found by its name.
const groupItem = (w: Mounted, block: string, group: string): DOMWrapper<Element> => {
  const found = w.findAll(`${block} li`).find((item) => item.find("span").text() === group)
  if (!found) throw new Error(`no group ${group} in ${block}`)
  return found
}

describe("OutputLevers", () => {
  it("offers auto and every playable reference of the episode", () => {
    const values = valuesOf(mountLevers().find(".lever-choice select"))
    expect(values[0]).toBe("")
    expect(values.slice(1).sort()).toEqual(["Ruchime|7|vibix", "Ruchime|7|vk", "Студия|9|kodik"])
  })

  it("names each reference by its group, its player and the highest height", () => {
    const labels = mountLevers().findAll(".lever-choice option").map((option) => option.text()).slice(1)
    expect(labels.sort()).toEqual([
      t("output.lever.referenceLabelHeight", { group: "Ruchime", player: "Vibix", height: 1080 }),
      t("output.lever.referenceLabelHeight", { group: "Ruchime", player: "VK", height: 1080 }),
      t("output.lever.referenceLabelHeight", { group: "Студия", player: "kodik", height: 720 }),
    ].sort())
  })

  it("shows the chosen reference as selected", () => {
    const w = mountLevers({ levers: [leverOf({ lever: LEVER.choice, value: CHOSEN, level: "series" })] })
    expect(selectedOf(w.find(".lever-choice select"))).toBe("Ruchime|7|vk")
  })

  it("shows auto when no reference is chosen", () => {
    expect(selectedOf(mountLevers().find(".lever-choice select"))).toBe("")
  })

  it("sends a chosen reference as its object", async () => {
    const w = mountLevers()
    await w.find(".lever-choice select").setValue("Студия|9|kodik")
    expect(w.emitted("set")?.[0]).toEqual([LEVER.choice, null, { group: "Студия", source_id: 9, player: "kodik" }])
  })

  it("sends auto as null", async () => {
    const w = mountLevers({ levers: [leverOf({ lever: LEVER.choice, value: CHOSEN, level: "season" })] })
    await w.find(".lever-choice select").setValue("")
    expect(w.emitted("set")?.[0]).toEqual([LEVER.choice, null, null])
  })

  it("keeps a chosen reference that the rows no longer offer as its own marked option", () => {
    const gone = { group: "Gone", source_id: 3, player: "vk" }
    const select = mountLevers({ levers: [leverOf({ lever: LEVER.choice, value: gone, level: "season" })] }).find(".lever-choice select")
    expect(valuesOf(select)).toHaveLength(5)
    expect(selectedOf(select)).toBe("Gone|3|vk")
    expect(select.findAll("option")[4].text()).toBe(
      t("output.lever.missing", { label: t("output.lever.referenceLabel", { group: "Gone", player: "vk" }) }),
    )
  })

  it("draws a participation switch per source player, on unless a level turns it off", () => {
    const w = mountLevers({ levers: [leverOf({ lever: LEVER.allow, target: playerTarget(7, "vk"), value: false, level: "season" })] })
    const boxes = w.findAll(".lever-allow input[type=checkbox]")
    expect(boxes.map(checkedOf)).toEqual([true, false, true])
    expect(w.findAll(".lever-allow li")[0].text()).toContain(
      t("output.lever.playerSource", { player: "Vibix", source: t("output.lever.source", { id: 7 }) }),
    )
  })

  it("sends the target and the new value of a participation switch", async () => {
    const w = mountLevers({ levers: [leverOf({ lever: LEVER.allow, target: playerTarget(7, "vk"), value: false, level: "season" })] })
    const boxes = w.findAll(".lever-allow input[type=checkbox]")
    await boxes[0].setValue(false)
    await boxes[1].setValue(true)
    expect(w.emitted("set")).toEqual([
      [LEVER.allow, playerTarget(7, "vibix"), false],
      [LEVER.allow, playerTarget(7, "vk"), true],
    ])
  })

  it("draws a take switch per voice-over group, on by default, and sends its group target", async () => {
    const w = mountLevers()
    expect(w.findAll(".lever-take input[type=checkbox]").map(checkedOf)).toEqual([true, true, true])
    await groupItem(w, ".lever-take", "Ruchime").find("input").setValue(false)
    expect(w.emitted("set")?.[0]).toEqual([LEVER.take, groupTarget("Ruchime"), false])
  })

  it("names the voice-over without a group by the catalog", () => {
    expect(mountLevers().find(".lever-take li").text()).toContain(t("output.lever.noGroup"))
  })

  it("draws a player choice per voice-over group with auto and the players of that group", () => {
    const w = mountLevers()
    expect(w.findAll(".lever-player select")).toHaveLength(3)
    expect(valuesOf(groupItem(w, ".lever-player", "Ruchime").find("select"))).toEqual(["", "7|vibix", "7|vk"])
    expect(valuesOf(groupItem(w, ".lever-player", "Студия").find("select"))).toEqual(["", "9|kodik"])
  })

  it("shows the player chosen for a group as selected", () => {
    const w = mountLevers({
      levers: [leverOf({ lever: LEVER.player, target: groupTarget("Студия"), value: { source_id: 9, player: "kodik" }, level: "episode" })],
    })
    expect(selectedOf(groupItem(w, ".lever-player", "Студия").find("select"))).toBe("9|kodik")
  })

  it("sends a chosen player as its object and auto as null", async () => {
    const w = mountLevers()
    const select = groupItem(w, ".lever-player", "Ruchime").find("select")
    await select.setValue("7|vk")
    await select.setValue("")
    expect(w.emitted("set")).toEqual([
      [LEVER.player, groupTarget("Ruchime"), { source_id: 7, player: "vk" }],
      [LEVER.player, groupTarget("Ruchime"), null],
    ])
  })

  it("keeps a chosen player that the group no longer offers as its own marked option", () => {
    const w = mountLevers({
      levers: [leverOf({ lever: LEVER.player, target: groupTarget("Ruchime"), value: { source_id: 7, player: "kodik" }, level: "series" })],
    })
    const select = groupItem(w, ".lever-player", "Ruchime").find("select")
    expect(selectedOf(select)).toBe("7|kodik")
    expect(select.findAll("option")[3].text()).toBe(
      t("output.lever.missing", { label: t("output.lever.playerSource", { player: "kodik", source: t("output.lever.source", { id: 7 }) }) }),
    )
  })

  it("names where each value comes from by its level", () => {
    const w = mountLevers({ levers: [leverOf({ lever: LEVER.allow, target: playerTarget(7, "vk"), value: false, level: "season" })] })
    const items = w.findAll(".lever-allow li")
    expect(items[1].find(".lever-level").text()).toBe(t("output.level.season"))
    expect(items[0].find(".lever-level").text()).toBe(t("output.level.default"))
  })

  it("offers the reset only for a value set on this level and sends its lever and target", async () => {
    const w = mountLevers({
      scope: "season",
      levers: [
        leverOf({ lever: LEVER.allow, target: playerTarget(7, "vk"), value: false, level: "season" }),
        leverOf({ lever: LEVER.allow, target: playerTarget(7, "vibix"), value: false, level: "series" }),
      ],
    })
    const items = w.findAll(".lever-allow li")
    expect(items[0].find("button").exists()).toBe(false)
    expect(items[1].find("button").exists()).toBe(true)
    await items[1].find("button").trigger("click")
    expect(w.emitted("reset")?.[0]).toEqual([LEVER.allow, playerTarget(7, "vk")])
  })

  it("resets the reference only when the level itself sets it", async () => {
    const withChoice = (level: "series" | "season") => mountLevers({
      scope: "season", levers: [leverOf({ lever: LEVER.choice, value: CHOSEN, level })],
    })
    expect(withChoice("series").find(".lever-choice button").exists()).toBe(false)
    const own = withChoice("season")
    await own.find(".lever-choice button").trigger("click")
    expect(own.emitted("reset")?.[0]).toEqual([LEVER.choice, null])
  })

  it("blocks every control while busy", () => {
    const controls = mountLevers({ busy: true }).findAll("select, input, button")
    expect(controls.length).toBeGreaterThan(0)
    expect(controls.every((control) => (control.element as HTMLInputElement).disabled)).toBe(true)
  })

  it("hides every block that has no choices", () => {
    expect(mountLevers({ view: viewOf([]) }).findAll("section")).toHaveLength(0)
  })

  it("keeps the reference block when only a chosen reference is left", () => {
    const w = mountLevers({ view: viewOf([]), levers: [leverOf({ lever: LEVER.choice, value: CHOSEN, level: "series" })] })
    expect(w.findAll("section")).toHaveLength(1)
    expect(w.find(".lever-choice").exists()).toBe(true)
  })
})
