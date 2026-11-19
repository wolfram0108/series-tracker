import { describe, expect, it } from "vitest"
import { merged, noted } from "./layoutParts"
import type { components } from "../api/schema"

type Composition = components["schemas"]["CompositionReply"]

const settingOf = (episode: number, value: string) => ({ season: 1, episode, levers: [{ lever: value, level: "series" }] })
const held = {
  series_id: 1, rev: 1, part: "whole", rows: [{ row_key: "r" }], sources: [{ source_id: 1 }], levels: [],
  settings: [settingOf(1, "old"), settingOf(2, "old"), settingOf(3, "old")], compilations: [], voice_orders: [], voice_titles: {},
} as unknown as Composition

describe("a part of the layout put into the layout a window holds", () => {
  it("replaces every field the part brings, whatever its name, and keeps every other", () => {
    const part = { series_id: 1, rev: 2, part: "levers", levels: [{ season: 1, levers: [] }], a_field_added_later: [7] } as unknown as Composition
    const now = merged(held, part, null) as unknown as Record<string, unknown>
    expect(now.rev).toBe(2)
    expect(now.levels).toEqual([{ season: 1, levers: [] }])
    expect(now.a_field_added_later).toEqual([7])
    expect(now.rows).toEqual([{ row_key: "r" }])  // the part does not bring the rows: they stand
    expect(now.settings).toBe(held.settings)  // nor the levers: they stand too
  })

  it("puts the levers of the episodes it names among those held, in the order of season and episode", () => {
    const part = { series_id: 1, rev: 2, part: "levers", settings: [settingOf(2, "new")] } as unknown as Composition
    const now = merged(held, part, new Set(["1:2"]))
    expect((now.settings ?? []).map((each) => [each.episode, each.levers?.[0]?.lever])).toEqual([[1, "old"], [2, "new"], [3, "old"]])
  })

  it("is the layout itself when it is the whole", () => {
    const whole = { ...held, rev: 5 } as Composition
    expect(merged(held, whole, null)).toBe(whole)
  })

  it("takes the widest part of the news and every episode they name", () => {
    const one = noted(undefined, { series_id: 1, rev: 2, part: "sources", summary_only: true, episodes: [] })
    const two = noted(one, { series_id: 1, rev: 3, part: "levers", summary_only: false, episodes: [{ season: 1, episode: 4 }] })
    expect(two.part).toBe("levers")
    expect([...(two.episodes ?? [])]).toEqual(["1:4"])
    expect(noted(two, { series_id: 1, rev: 4, part: "levers", summary_only: false, episodes: [] }).episodes).toBeNull()
  })
})
