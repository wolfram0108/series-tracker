import { describe, it, expect } from "vitest"
import { sanitizeName, sanitizePath } from "./fsnames"

// Same tables as tests/test_fsnames.py: the two implementations must agree.
const NAME_CASES: [string, string][] = [
  ['Bad: "Name"?', "Bad Name"],
  ["Star Trek: Strange New Worlds", "Star Trek Strange New Worlds"],
  ["What If...?", "What If..."],
  ["a/b\\c|d<e>f*g", "abcdefg"],
  ["  За гранью времени  ", "За гранью времени"],
  ["a \t: b", "a b"],
  ["a\x00b\x1fc", "ab c"], // \x1f is whitespace, \x00 is not
  ["", ""],
  ["???", ""],
]

const PATH_CASES: [string, string][] = [
  [
    "/nas/Series/test/Звёздный путь: Странные новые миры (2022) [tmdbid-1]",
    "/nas/Series/test/Звёздный путь Странные новые миры (2022) [tmdbid-1]",
  ],
  ["C:\\media\\show", "C/media/show"],
  ["/nas/Series fin /x", "/nas/Series fin/x"],
  ["/nas/a?b/c*d|e", "/nas/ab/cde"],
  ["/nas/Series/test/", "/nas/Series/test/"],
  ["  /nas/x  ", "/nas/x"],
  ["", ""],
]

describe("sanitizeName", () => {
  it.each(NAME_CASES)("%j → %j", (raw, expected) => {
    expect(sanitizeName(raw)).toBe(expected)
    expect(sanitizeName(expected)).toBe(expected) // idempotent
  })
})

describe("sanitizePath", () => {
  it.each(PATH_CASES)("%j → %j", (raw, expected) => {
    expect(sanitizePath(raw)).toBe(expected)
    expect(sanitizePath(expected)).toBe(expected) // idempotent
  })
})
