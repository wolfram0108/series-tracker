import { readFileSync } from "node:fs"
import { describe, expect, it } from "vitest"
import { hasMessage } from "../i18n"
import { capital, QBIT_STATES, SERIES_STATES, STATE_NAMES, stateLabel, stateOf, stateWord } from "./states"

const styles = (name: string) => readFileSync(new URL(`../styles/${name}`, import.meta.url), "utf8")

describe("the vocabulary of states", () => {
  it("gives every state a word, a colour and the rule that sets the colour on an element", () => {
    const tokens = styles("tokens.css")
    const rules = styles("states.css")
    for (const name of STATE_NAMES) {
      expect(hasMessage(`state.${name}`), name).toBe(true)
      for (const part of ["", "-end", "-ink"]) expect(tokens, `${name}${part}`).toContain(`--state-${name}${part}:`)
      expect(rules, name).toContain(`[data-state="${name}"]`)
    }
  })

  it("keeps one word for a state and raises its first letter where it stands alone", () => {
    expect(stateWord("done")).toBe("готово")
    expect(stateLabel("done")).toBe("Готово")
    expect(capital("")).toBe("")
  })

  it("turns a code a module told into a state, and a code it does not know into none", () => {
    expect(stateOf(SERIES_STATES, "scanning")).toBe("sync")
    expect(stateOf(QBIT_STATES, "stalledDL")).toBe("idle")
    expect(stateOf(QBIT_STATES, "unknown")).toBeNull()
    expect(stateOf(QBIT_STATES, "toString")).toBeNull()
    expect(stateOf(QBIT_STATES, null)).toBeNull()
  })
})
