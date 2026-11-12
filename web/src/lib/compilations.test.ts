import { describe, expect, it } from "vitest"
import { badgeOf, cardStatus, letterOf, membersOf, shareOf, takes, type CompilationView } from "./compilations"
import type { PlanCut } from "./plan"

function view(index: number, start: number, end: number, members: number[]): CompilationView {
  return {
    season: 1, index, take: true,
    ref: { key: `k${index}`, source_id: 1, row_key: "r", player: "vk", voice_group: "ПВА", height: 2160, start, end },
    episodes: Array.from({ length: end - start + 1 }, (_, at) => ({
      episode: start + at, member: members.includes(start + at), absence: members.includes(start + at) ? null : "elsewhere",
      elsewhere: null, own: false,
    })),
  }
}

describe("compilations", () => {
  it("names compilations by letters that go on past Z", () => {
    expect([0, 1, 25, 26, 27].map(letterOf)).toEqual(["A", "B", "Z", "AA", "AB"])
  })

  it("gives colours round the sixteen tokens and finds the compilation of each episode", () => {
    expect(badgeOf(view(17, 1, 2, [])).color).toBe(2)
    const members = membersOf([view(0, 1, 3, [1, 2]), view(1, 3, 4, [3])])
    expect([members.get("1:1")?.letter, members.get("1:3")?.letter, members.get("1:4")]).toEqual(["A", "B", undefined])
  })

  it("does not take an episode whose reference now is as high as the compilation, though the layout puts it in", () => {
    const compilation = view(0, 1, 3, [1, 2, 3])
    const height = (_season: number, episode: number) => (episode === 3 ? 2160 : episode === 2 ? 720 : null)
    expect([1, 2, 3].map((episode) => takes(compilation, episode, height))).toEqual([true, true, false])
    expect([...membersOf([compilation], height).keys()]).toEqual(["1:1", "1:2"])
    expect(takes(compilation, 1, () => "off")).toBe(false)  // an episode switched off takes nothing
  })

  it("colours a card by its cut and turns off an episode that is not in the compilation", () => {
    const cut: PlanCut = { season: 1, episode: 1, part: 0, cut_key: "k", status: "aligning" }
    expect(cardStatus(false, cut)).toBe("off")
    expect(cardStatus(true, cut)).toBe("align")
    expect(cardStatus(true, undefined)).toBe("wait")
    expect(cardStatus(true, undefined, { key: "k" })).toBe("ready")
    expect(shareOf({ ...cut, from_ref_s: 1260, from_dub_s: 40 })).toBeCloseTo(0.969, 3)
  })
})
