import type { components } from "../api/schema"
import { t, type MessageKey } from "../i18n"
import { ru } from "../i18n/ru"
import { AVAILABILITY_STATES, stateOf, stateWord } from "./states"

/** Why something is unavailable or refused: a code of the core and the facts that fill its text. */
export type Reason = components["schemas"]["Reason"]

function coded(text: string): Reason | null {
  if (!text.startsWith("{")) return null
  try {
    const { code, ...params } = JSON.parse(text)
    return typeof code === "string" ? { code, params } : null
  } catch {
    return null
  }
}

/** The wording of a reason, given as the core sends it or as the JSON text that travels in reports.
 *  Words of other systems (code `external`, or text that is not a coded reason) are shown as they came. */
export function reasonText(reason: Reason | string | null | undefined): string {
  if (reason == null || reason === "") return ""
  const found = typeof reason === "string" ? (coded(reason) ?? { code: "external", params: { text: reason } }) : reason
  const params = found.params ?? {}
  if (found.code === "external") return String(params.text ?? "")
  const key = `reason.${found.code}`
  if (!(key in ru)) return t("reason.unknown", { code: found.code })
  const worded = (fact: unknown): unknown => {
    if (Array.isArray(fact)) return fact.map(worded).join("; ")
    return typeof fact === "string" && coded(fact) ? reasonText(fact) : fact
  }
  const facts: Record<string, unknown> = Object.fromEntries(Object.entries(params).map(([name, fact]) => [name, worded(fact)]))
  // A refused template names its block, the block before it and its place by their codes (calc/rules/engine.py).
  for (const fact of ["block", "after"]) {
    const block = `rules.block.${String(params[fact] ?? "")}.label`
    if (fact in params && block in ru) facts[fact] = t(block as MessageKey)
  }
  const place = `rules.place.${String(params.place ?? "")}`
  if ("place" in params && place in ru) facts.place = t(place as MessageKey)
  // A refused action names the action and the state of the object by their codes: the window words both.
  if (found.code === "not_allowed") {
    const action = `availability.action.${String(params.action ?? "")}`
    if (action in ru) facts.action = t(action as MessageKey)
    const state = stateOf(AVAILABILITY_STATES, String(params.state ?? ""))
    if (state) facts.state = stateWord(state)
  }
  return t(key as MessageKey, facts as Record<string, string | number>)
}
