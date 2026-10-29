import type { components } from "../api/schema"
import { t, type MessageKey } from "../i18n"
import { ru } from "../i18n/ru"

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
  const facts = Object.fromEntries(Object.entries(params).map(([name, fact]) => [name, worded(fact)]))
  return t(key as MessageKey, facts as Record<string, string | number>)
}
