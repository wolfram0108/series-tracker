// How the answer of a profile to one line is shown in the test of rules. The numbers are the constructor's alone: the
// window only names them, it computes none.
import type { components } from "../api/schema"
import { t, type MessageKey } from "../i18n"

type Schemas = components["schemas"]

export type TestResult = Schemas["RuleResult"]

export interface Pill {
  key: string
  label: string
  value: string
  icon: string
}

export type CardState = "no-match" | "excluded" | "success"

/** An exclusion is the last word; a line no rule touched has no events at all. */
export function cardState(result: TestResult | null | undefined): CardState {
  if (!result) return "no-match"
  if (result.excluded) return "excluded"
  return (result.events ?? []).length === 0 ? "no-match" : "success"
}

/** The name of the rule that excluded the line; empty when none did. */
export function excludingRule(result: TestResult | null | undefined): string {
  const event = (result?.events ?? []).find((each) => each.action === "exclude")
  return typeof event?.rule === "string" ? event.rule : ""
}

// Fields the constructor may add besides the numbers; shown only when they hold a word or a number.
const shown = (value: unknown): string | null =>
  typeof value === "string" && value !== "" ? value : typeof value === "number" ? String(value) : null

/** What the profile found in the line, one pill per found field. */
export function pillsOf(result: TestResult | null | undefined): Pill[] {
  if (!result) return []
  const pills: Pill[] = []
  const add = (key: string, label: string, value: string | null, icon: string): void => {
    if (value !== null) pills.push({ key, label, value, icon })
  }
  add("season", t("rulesTest.pill.season"), shown(result.season), "pi pi-th-large")
  add("episode", t("rulesTest.pill.episode"), shown(result.episode), "pi pi-video")
  if (result.start != null) add("range", t("rulesTest.pill.range"), `${result.start}-${result.end ?? result.start}`, "pi pi-list")
  add("part", t("rulesTest.pill.part"), shown(result.part), "pi pi-clone")
  add("voiceover", t("rulesTest.pill.voiceover"), shown(result.voiceover), "pi pi-tags")
  add("quality", t("rulesTest.pill.quality"), shown(result.quality), "pi pi-star")
  add("resolution", t("rulesTest.pill.resolution"), shown(result.resolution), "pi pi-arrows-h")
  return pills
}

/** The badge of the card: the best resolution the page gives the row, named by its class (4K, QHD, FHD, HD, SD); null when it gives none. */
export function resolutionBadge(heights: number[] | null | undefined): string | null {
  const top = heights && heights.length > 0 ? Math.max(...heights) : 0
  if (top <= 0) return null
  const key = top >= 2160 ? "uhd" : top >= 1440 ? "qhd" : top >= 1080 ? "fhd" : top >= 720 ? "hd" : top >= 480 ? "sd" : "low"
  return t(`rulesTest.resolution.${key}` as MessageKey, { height: top })
}
