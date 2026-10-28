import type { components } from "../api/schema"
import { t, type MessageKey } from "../i18n"
import { ru } from "../i18n/ru"

/** Why a voice-over is in its state: a code of the core and the facts that fill its text. */
export type VoiceReason = components["schemas"]["Reason"]

/** The wording of a reason; words of other systems (code `external`) are shown as they came. */
export function reasonText(reason: VoiceReason): string {
  const params = reason.params ?? {}
  if (reason.code === "external") return String(params.text ?? "")
  const key = `voices.reason.${reason.code}`
  if (key in ru) return t(key as MessageKey, params)
  return t("voices.reason.unknown", { code: reason.code })
}
