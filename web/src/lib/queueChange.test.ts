import { describe, expect, it } from "vitest"
import type { QueueRow } from "./output"
import { bodyOf, changeKey, coreValue, levelOf, queueKey, scopeKey, withChanges, type QueueChange, type Queues } from "./queueChange"

const row = (player: string, over: Partial<QueueRow> = {}): QueueRow => ({
  group: "Ruchime", source_id: 7, player, player_label: player, episodes: "", stale: "", allowed: true, allowed_level: "default", language: "", subtitle_type: "", ...over,
})

const queues = (): Queues => ({
  series_id: 1, season: 1, episode: null, compilation: null,
  reference: { rows: [row("vk"), row("kodik")], order_level: "default", same_level: "default" },
  markup: { rows: [row("vk"), row("kodik")], order_level: "default", same_level: "default" },
  groups: [
    { group: "Ruchime", rows: [row("vk"), row("kodik")], order_level: "default", same_level: "default", take: true, take_level: "default" },
    { group: "AniStar", rows: [row("vk", { group: "AniStar" })], order_level: "default", same_level: "default", take: true, take_level: "default" },
  ],
  markup_follows: true,
})

const allow = (queue: "reference" | "markup" | "voice", player: string, value: boolean, group = "Ruchime"): QueueChange => ({
  kind: "allow", scope: { season: 1 }, queue, lever: `${queue}.allow`, target: `variant:${group}:7:${player}`, value, group, source_id: 7, player,
})
const take = (group: string, value: boolean): QueueChange => ({
  kind: "take", scope: { season: 1 }, queue: "voice", lever: "voice.take", target: `group:${group}`, value, group,
})

describe("the key of a level and of a change", () => {
  it("names a level by its place, and a change by its level, lever and target", () => {
    expect(queueKey(1, null)).toBe(scopeKey({ season: 1 }))
    expect(scopeKey({})).toBe(queueKey(null, null, null))
    expect(scopeKey({ season: 1, episode: 2 })).not.toBe(scopeKey({ season: 1, compilation: "k" }))
    expect(changeKey(allow("reference", "vk", false))).toBe(changeKey(allow("reference", "vk", true)))
    expect(changeKey(allow("reference", "vk", false))).not.toBe(changeKey(allow("reference", "kodik", false)))
    expect(changeKey({ ...allow("reference", "vk", false), scope: { season: 2 } })).not.toBe(changeKey(allow("reference", "vk", false)))
  })

  it("tells the level of a scope", () => {
    expect([{}, { season: 1 }, { season: 1, episode: 2 }, { season: 1, compilation: "k" }].map(levelOf))
      .toEqual(["series", "season", "episode", "compilation"])
  })

  it("builds the body of the lever the core reads", () => {
    expect(bodyOf(allow("reference", "vk", false))).toStrictEqual({
      season: 1, lever: "reference.allow", target: "variant:Ruchime:7:vk", value: false })
  })
})

describe("coreValue", () => {
  it("reads the box of a variant of each queue, and of a group, from the queues of the core", () => {
    const own = queues()
    own.groups![1].take = false
    own.markup!.rows![1].allowed = false
    expect(coreValue(own, allow("reference", "kodik", true))).toBe(true)
    expect(coreValue(own, allow("markup", "kodik", true))).toBe(false)
    expect(coreValue(own, take("AniStar", true))).toBe(false)
    expect(coreValue(own, allow("voice", "vk", true, "AniStar"))).toBe(true)
  })

  it("knows nothing of a variant the queues do not list, or of queues not read", () => {
    expect(coreValue(queues(), allow("reference", "allvideo", false))).toBeUndefined()
    expect(coreValue(queues(), take("Nobody", false))).toBeUndefined()
    expect(coreValue(undefined, take("AniStar", false))).toBeUndefined()
  })
})

describe("withChanges", () => {
  it("shows a cleared box of the reference at the level of the change, and leaves the other queues as they are", () => {
    const base = queues()
    const shown = withChanges(base, [allow("reference", "vk", false)])
    expect(shown.reference!.rows!.map((each) => [each.player, each.allowed, each.allowed_level])).toEqual([
      ["vk", false, "season"], ["kodik", true, "default"]])
    expect(shown.markup).toBe(base.markup)
    expect(shown.groups).toBe(base.groups)
    expect(base.reference!.rows![0].allowed).toBe(true) // the queues of the core are not written to
  })

  it("shows the boxes of the markup and of one voice-over group, and the take of a group", () => {
    const shown = withChanges(queues(), [allow("markup", "kodik", false), allow("voice", "vk", false, "AniStar"), take("Ruchime", false)])
    expect(shown.markup!.rows!.map((each) => each.allowed)).toEqual([true, false])
    expect(shown.groups!.map((each) => [each.group, each.take, each.take_level, each.rows!.map((one) => one.allowed)])).toEqual([
      ["Ruchime", false, "season", [true, true]], ["AniStar", true, "default", [false]]])
  })

  it("keeps the very objects that no change touches, and the very queues when nothing differs", () => {
    const base = queues()
    const shown = withChanges(base, [allow("reference", "kodik", false)])
    expect(shown.reference!.rows![0]).toBe(base.reference!.rows![0])
    expect(withChanges(base, [allow("reference", "vk", true), take("Ruchime", true)])).toBe(base)
    expect(withChanges(base, [])).toBe(base)
  })

  it("takes the later change of a box, and ignores a change of a variant that the queue does not list", () => {
    const shown = withChanges(queues(), [allow("reference", "vk", false), allow("reference", "vk", true), allow("reference", "gone", false)])
    expect(shown.reference!.rows!.map((each) => each.allowed)).toEqual([true, true])
  })
})
