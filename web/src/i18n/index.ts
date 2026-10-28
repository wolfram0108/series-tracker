import { ru, type MessageKey } from "./ru"

export type { MessageKey }

/** Text of a catalog key; `{name}` placeholders are filled from `params`. Language is the only runtime parameter. */
export function t(key: MessageKey, params: Record<string, string | number> = {}): string {
  return ru[key].replace(/\{(\w+)\}/g, (_, name: string) => String(params[name] ?? ""))
}
