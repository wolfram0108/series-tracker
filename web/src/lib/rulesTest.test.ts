import { describe, expect, it } from "vitest"
import { cardState, excludingRule, pillsOf, resolutionBadge, type TestResult } from "./rulesTest"

const result = (over: Partial<TestResult> = {}): TestResult => ({ excluded: false, events: [], errors: [], ...over })
const extracted = { rule: "серия", action: "extract" }

describe("cardState", () => {
  it("says no rule applied for a line with no answer or no events, and the exclusion before anything else", () => {
    expect(cardState(null)).toBe("no-match")
    expect(cardState(result())).toBe("no-match")
    expect(cardState(result({ events: [extracted] }))).toBe("success")
    expect(cardState(result({ excluded: true, events: [{ rule: "трейлер", action: "exclude" }] }))).toBe("excluded")
  })
})

describe("excludingRule", () => {
  it("names the rule that excluded the line, and nothing for a line that is not excluded", () => {
    expect(excludingRule(result({ excluded: true, events: [extracted, { rule: "трейлер", action: "exclude" }] }))).toBe("трейлер")
    expect(excludingRule(result({ events: [extracted] }))).toBe("")
    expect(excludingRule(null)).toBe("")
  })
})

describe("pillsOf", () => {
  it("makes a pill of every field the profile found, the range from its two ends, and keeps season zero", () => {
    const pills = pillsOf(result({ season: 0, start: 1, end: 3, part: 2, voiceover: "Мой дубляж", quality: "WEB-DL", resolution: 1080 }))
    expect(pills.map((pill) => [pill.key, pill.value])).toEqual([
      ["season", "0"], ["range", "1-3"], ["part", "2"], ["voiceover", "Мой дубляж"], ["quality", "WEB-DL"], ["resolution", "1080"],
    ])
  })

  it("takes a range with no end as the one episode and shows nothing for fields the profile did not find", () => {
    expect(pillsOf(result({ start: 4 })).map((pill) => pill.value)).toEqual(["4-4"])
    expect(pillsOf(result({ quality: "" }))).toEqual([])
    expect(pillsOf(null)).toEqual([])
  })
})

describe("resolutionBadge", () => {
  it("names the best resolution of the row by its class and says nothing for a row with none", () => {
    expect(resolutionBadge([720, 2160, 1080])).toBe("4K 2160")
    expect(resolutionBadge([1440, 1080])).toBe("QHD 1440")
    expect(resolutionBadge([1080])).toBe("FHD 1080")
    expect(resolutionBadge([480, 720])).toBe("HD 720")
    expect(resolutionBadge([360, 480])).toBe("SD 480")
    expect(resolutionBadge([240])).toBe("240p")
    expect([resolutionBadge([]), resolutionBadge(null), resolutionBadge(undefined)]).toEqual([null, null, null])
  })
})
