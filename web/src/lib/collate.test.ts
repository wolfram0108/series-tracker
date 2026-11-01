import { describe, expect, it } from "vitest"
import { compareNames } from "./collate"

describe("compareNames", () => {
  it("orders by the Russian rules whatever the locale of the machine: Cyrillic before Latin, case aside", () => {
    expect(["Ruchime", "Студия", "anime", "Анидаб"].sort(compareNames)).toEqual(["Анидаб", "Студия", "anime", "Ruchime"])
    expect(compareNames("Студия", "студия")).not.toBe(0)
    expect(compareNames("a", "a")).toBe(0)
  })
})
