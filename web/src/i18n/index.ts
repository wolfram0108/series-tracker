import { ru, type MessageKey } from "./ru"

export type { MessageKey }

/** Text of a catalog key; `{name}` placeholders are filled from `params`. Language is the only runtime parameter. */
export function t(key: MessageKey, params: Record<string, string | number> = {}): string {
  return ru[key].replace(/\{(\w+)\}/g, (_, name: string) => String(params[name] ?? ""))
}

/** The base of a key that has the forms `<base>.one`, `<base>.few` and `<base>.many`. */
export type PluralBase = MessageKey extends infer K ? (K extends `${infer B}.one` ? B : never) : never

const plurals = new Intl.PluralRules("ru")

/** Text of the plural form that the language picks for `count`; `{count}` is filled in. A form the rule names
 *  `other` (a fraction) takes the `many` text. */
export function tn(base: PluralBase, count: number, params: Record<string, string | number> = {}): string {
  const form = plurals.select(count)
  return t(`${base}.${form === "other" ? "many" : form}` as MessageKey, { count, ...params })
}
