// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { mount, type DOMWrapper } from "@vue/test-utils"
import OutputLevers from "./OutputLevers.vue"
import { t } from "../../i18n"
import { groupTarget, isDrawn, variantTarget, type EpisodeView, type Lever, type LeverSpec, type Row } from "../../lib/output"
import { SPECS } from "../../lib/output.fixtures"

// A playable single-episode line; each test changes only the fields it is about.
const rowOf = (over: Partial<Row>): Row => ({
  series_id: 1, source_id: 7, row_key: "a", excluded: false, season: 1, episode_start: 5, episode_end: 5,
  voice_label: "Ruchime", voice_group: over.voice_label ?? "Ruchime", voice_name: "Ruchime", player: "vk", player_label: "VK",
  group: "", text: "", link: "", state: "playable", heights: [1080], ...over,
})

// Source 7 has two players of one group; source 9 has a voice-over of its own, a compilation of it, and a line absent.
const ROWS: Row[] = [
  rowOf({ row_key: "a", source_id: 7, player: "vk", player_label: "VK", heights: [720, 1080] }),
  rowOf({ row_key: "b", source_id: 7, player: "vibix", player_label: "Vibix", heights: [1080] }),
  rowOf({ row_key: "c", source_id: 9, player: "kodik", player_label: "", voice_label: "Студия", heights: [720] }),
  rowOf({ row_key: "d", source_id: 9, player: "kodik", player_label: "", voice_label: "", state: "absent", heights: [] }),
  rowOf({ row_key: "e", source_id: 9, player: "cvh", player_label: "CVH", voice_label: "Студия", episode_start: 4, episode_end: 6, heights: [1080] }),
]

const viewOf = (rows: Row[] = ROWS): EpisodeView => ({ season: 1, episode: 5, references: [], rows, levers: [], dubs: [] })

const leverOf = (over: Partial<Lever>): Lever => ({ lever: "voice.take", target: null, value: null, level: "default", ...over })

interface Mounting {
  scope?: "series" | "season" | "episode"
  view?: EpisodeView
  levers?: Lever[]
  specs?: LeverSpec[]
  busy?: boolean
}

const mountLevers = (over: Mounting = {}) =>
  mount(OutputLevers, { props: { scope: "season" as const, view: viewOf(), levers: [], specs: SPECS, busy: false, ...over } })
type Mounted = ReturnType<typeof mountLevers>

const checkedOf = (box: DOMWrapper<Element>): boolean => (box.element as HTMLInputElement).checked
// The order of columns follows the locale of the environment, so a column is found by its group.
const columnOf = (w: Mounted, group: string): DOMWrapper<Element> => {
  const found = w.findAll(".voice-column").find((column) => column.attributes("data-group") === group)
  if (!found) throw new Error(`no column ${group}`)
  return found
}
const variantOf = (w: Mounted, group: string, text: string): DOMWrapper<Element> => {
  const found = columnOf(w, group).findAll(".variant").find((item) => item.find(".variant-name").text() === text)
  if (!found) throw new Error(`no variant ${text} in ${group}`)
  return found
}
const VK = t("output.variant.labelHeight", { player: "VK", source: t("output.lever.source", { id: 7 }), height: 1080 })
const VIBIX = t("output.variant.labelHeight", { player: "Vibix", source: t("output.lever.source", { id: 7 }), height: 1080 })
const KODIK = t("output.variant.labelHeight", { player: "kodik", source: t("output.lever.source", { id: 9 }), height: 720 })
const mark = (item: DOMWrapper<Element>, kind: "reference" | "prefer"): DOMWrapper<HTMLButtonElement> =>
  item.find(`.variant-${kind}`) as DOMWrapper<HTMLButtonElement>

describe("OutputLevers", () => {
  it("makes a column of every voice-over group of the view, with the variants it can be had from", () => {
    const w = mountLevers()
    expect(w.findAll(".voice-column").map((column) => column.attributes("data-group")).sort()).toEqual(["Ruchime", "Студия", ""].sort())
    expect(columnOf(w, "Ruchime").findAll(".variant-name").map((each) => each.text()).sort()).toEqual([VIBIX, VK].sort())
    expect(columnOf(w, "").find("h4").text()).toBe(t("output.lever.noGroup"))
  })

  it("names a variant by its player, its source and the highest height of its lines", () => {
    const w = mountLevers()
    expect(variantOf(w, "Ruchime", VK).exists()).toBe(true)
    expect(variantOf(w, "Студия", KODIK).exists()).toBe(true)
  })

  it("lets only a variant with a playable single episode be the reference, and the compilation's variant not", () => {
    const w = mountLevers()
    expect(mark(variantOf(w, "Ruchime", VK), "reference").attributes("disabled")).toBeUndefined()
    const cvh = variantOf(w, "Студия", t("output.variant.labelHeight", { player: "CVH", source: t("output.lever.source", { id: 9 }), height: 1080 }))
    expect(mark(cvh, "reference").attributes("disabled")).toBeDefined()
    expect(mark(cvh, "prefer").attributes("disabled")).toBeUndefined()
  })

  it("sets the reference to the group, the source and the player of the variant, and is pressed on the one it is", () => {
    const w = mountLevers()
    void mark(variantOf(w, "Ruchime", VIBIX), "reference").trigger("click")
    expect(w.emitted("set")?.[0]).toEqual(["reference.choice", null, { group: "Ruchime", source_id: 7, player: "vibix" }])
    const chosen = mountLevers({ levers: [leverOf({ lever: "reference.choice", value: { group: "Ruchime", source_id: 7, player: "vk" }, level: "series" })] })
    expect(mark(variantOf(chosen, "Ruchime", VK), "reference").attributes("aria-pressed")).toBe("true")
    expect(mark(variantOf(chosen, "Ruchime", VIBIX), "reference").attributes("aria-pressed")).toBe("false")
    expect(mark(variantOf(chosen, "Студия", KODIK), "reference").attributes("aria-pressed")).toBe("false")
  })

  it("says auto for the reference no level picked, and sets auto or resets it on request", async () => {
    const w = mountLevers()
    expect(w.find(".lever-reference .lever-value").text()).toBe(t("output.lever.auto"))
    expect(w.find(".lever-reference .lever-auto").exists()).toBe(false)
    const picked = mountLevers({ levers: [leverOf({ lever: "reference.choice", value: { group: "Ruchime", source_id: 7, player: "vk" }, level: "season" })] })
    expect(picked.find(".lever-reference .lever-value").text()).toBe(t("output.lever.referenceLabel", { group: "Ruchime", player: VK }))
    await picked.find(".lever-reference .lever-auto").trigger("click")
    await picked.find(".lever-reference .lever-reset").trigger("click")
    expect(picked.emitted("set")?.[0]).toEqual(["reference.choice", null, null])
    expect(picked.emitted("reset")?.[0]).toEqual(["reference.choice", null])
  })

  it("keeps a reference the view no longer offers listed and marked", () => {
    const w = mountLevers({ levers: [leverOf({ lever: "reference.choice", value: { group: "Ruchime", source_id: 99, player: "vk" }, level: "series" })] })
    expect(w.find(".lever-reference .lever-value").text()).toBe(t("output.lever.missing", { label: "Ruchime — vk" }))
  })

  it("lets every variant take part until a level says otherwise, and sends the choice with the target of the variant", async () => {
    const w = mountLevers()
    const box = variantOf(w, "Ruchime", VK).find("input")
    expect(checkedOf(box)).toBe(true)
    await box.setValue(false)
    expect(w.emitted("set")?.[0]).toEqual(["variant.allow", variantTarget("Ruchime", 7, "vk"), false])
  })

  it("shows a variant left out by a level, with the level, and a reset only where this level set it", async () => {
    const levers = [
      leverOf({ lever: "variant.allow", target: variantTarget("Ruchime", 7, "vk"), value: false, level: "season" }),
      leverOf({ lever: "variant.allow", target: variantTarget("Ruchime", 7, "vibix"), value: false, level: "series" }),
    ]
    const w = mountLevers({ levers })
    const vk = variantOf(w, "Ruchime", VK)
    expect(checkedOf(vk.find("input"))).toBe(false)
    expect(vk.find(".lever-level").text()).toBe(t("output.level.season"))
    expect(vk.find(".lever-reset").exists()).toBe(true)
    expect(variantOf(w, "Ruchime", VIBIX).find(".lever-reset").exists()).toBe(false)
    await vk.find(".lever-reset").trigger("click")
    expect(w.emitted("reset")?.[0]).toEqual(["variant.allow", variantTarget("Ruchime", 7, "vk")])
  })

  it("takes every group until a level says otherwise, with the choice sent for the target of the group", async () => {
    const w = mountLevers()
    const box = columnOf(w, "Студия").find(".voice-head input")
    expect(checkedOf(box)).toBe(true)
    await box.setValue(false)
    expect(w.emitted("set")?.[0]).toEqual(["voice.take", groupTarget("Студия"), false])
  })

  it("prefers a variant of a group by setting the pick of the group, and shows auto when none is picked", async () => {
    const w = mountLevers()
    expect(columnOf(w, "Ruchime").find(".voice-head").text()).toContain(t("output.lever.auto"))
    await mark(variantOf(w, "Ruchime", VIBIX), "prefer").trigger("click")
    expect(w.emitted("set")?.[0]).toEqual(["voice.player", groupTarget("Ruchime"), { source_id: 7, player: "vibix" }])
    const picked = mountLevers({ levers: [leverOf({ lever: "voice.player", target: groupTarget("Ruchime"), value: { source_id: 7, player: "vk" }, level: "episode" })], scope: "episode" })
    expect(mark(variantOf(picked, "Ruchime", VK), "prefer").attributes("aria-pressed")).toBe("true")
    expect(mark(variantOf(picked, "Ruchime", VIBIX), "prefer").attributes("aria-pressed")).toBe("false")
    expect(columnOf(picked, "Ruchime").find(".voice-head").text()).toContain(VK)
    await columnOf(picked, "Ruchime").find(".voice-head .lever-auto").trigger("click")
    await columnOf(picked, "Ruchime").find(".voice-head .lever-reset").trigger("click")
    expect(picked.emitted("set")?.[0]).toEqual(["voice.player", groupTarget("Ruchime"), null])
    expect(picked.emitted("reset")?.[0]).toEqual(["voice.player", groupTarget("Ruchime")])
  })

  it("offers to make a group in the head of a column of a voice-over that is in none, and in no other", () => {
    const rows = [
      rowOf({ row_key: "a", voice_label: "AniStar", voice_group: "AniStar" }),
      rowOf({ row_key: "b", voice_label: "Неизвестная озвучка 1", voice_group: null, voice_name: "Animy" }),
      rowOf({ row_key: "c", voice_label: "Неизвестная озвучка 2", voice_group: null, voice_name: "" }),
    ]
    const w = mountLevers({ view: viewOf(rows) })
    expect(columnOf(w, "AniStar").find(".group-maker").exists()).toBe(false)
    expect(columnOf(w, "Неизвестная озвучка 1").find(".group-maker").exists()).toBe(true)
    expect(columnOf(w, "Неизвестная озвучка 2").find(".group-maker").exists()).toBe(false)
    expect(columnOf(w, "Неизвестная озвучка 2").text()).toContain(t("voices.group.nameless"))
  })

  it("draws a lever registered later from its description alone, titled by its own name until the catalog has a title", () => {
    const later: LeverSpec = { name: "variant.mute", target: "variant", shape: "flag", default: false, consumer: "voices" }
    const w = mountLevers({ specs: [...SPECS, later] })
    const vk = variantOf(w, "Ruchime", VK)
    expect(vk.findAll("input").length).toBe(2)
    expect(vk.text()).toContain("variant.mute")
    expect(checkedOf(vk.findAll("input")[1])).toBe(false)
  })

  it("disables every control while a change is on its way", () => {
    const w = mountLevers({ busy: true })
    for (const control of [...w.findAll("input"), ...w.findAll(".variant-mark")]) {
      expect(control.attributes("disabled")).toBeDefined()
    }
  })

  it("says so when it cannot draw a lever of a form, and draws nothing for a view without lines", () => {
    const odd: LeverSpec = { name: "odd.lever", target: "group", shape: "reference_pick", default: null, consumer: "scan" }
    const w = mountLevers({ specs: [odd], view: viewOf([]) })
    expect(w.findAll(".voice-column").length).toBe(0)
    expect(w.findAll("input, button").length).toBe(0)
    expect(w.find(".lever-unsupported").text()).toBe(t("output.lever.unsupported", { name: "odd.lever" }))
  })

  it("draws a lever of the form without a target and with a flag once, with its level, and sets it with no target", async () => {
    const flag: LeverSpec = { name: "review.flag", target: "none", shape: "flag", default: false, consumer: "scan" }
    const w = mountLevers({ specs: [...SPECS, flag] })
    const box = w.find(".lever-general input")
    expect(checkedOf(box)).toBe(false)
    expect(w.find(".lever-general .lever-level").text()).toBe(t("output.level.default"))
    await box.setValue(true)
    expect(w.emitted("set")).toEqual([["review.flag", null, true]])
  })

  it("shows the flag without a target as it stands at the level and offers its own value to be reset", async () => {
    const flag: LeverSpec = { name: "review.flag", target: "none", shape: "flag", default: false, consumer: "scan" }
    const w = mountLevers({ specs: [flag], levers: [leverOf({ lever: "review.flag", value: true, level: "season" })] })
    expect(checkedOf(w.find(".lever-general input"))).toBe(true)
    await w.find(".lever-general .lever-reset").trigger("click")
    expect(w.emitted("reset")).toEqual([["review.flag", null]])
  })

  it("never leaves a lever of any form out without a word: each is drawn as a control or named as not drawn", () => {
    const targets: LeverSpec["target"][] = ["none", "group", "variant"]
    const shapes: LeverSpec["shape"][] = ["flag", "variant_pick", "reference_pick"]
    for (const target of targets) {
      for (const shape of shapes) {
        const spec: LeverSpec = { name: `form.${target}.${shape}`, target, shape, default: null, consumer: "scan" }
        const w = mountLevers({ specs: [spec] })
        const controls = w.findAll(".lever-pick input, .variant-mark").length  // the controls of levers, not the other fields
        const said = w.find(".lever-unsupported").exists()
        expect(controls > 0 || said, `${target}/${shape}`).toBe(true)
        expect(controls > 0 && said, `${target}/${shape}`).toBe(false)
        expect(said, `${target}/${shape}`).toBe(!isDrawn(spec))
      }
    }
  })
})
