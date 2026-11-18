import { ru, type MessageKey } from "./ru"

export type { MessageKey }

/** Text of a catalog key; `{name}` placeholders are filled from `params`. Language is the only runtime parameter. A key built at
 *  run time that the catalog lacks (a value the core added after this build) is shown as the key: one word, not a broken screen. */
export function t(key: MessageKey, params: Record<string, string | number> = {}): string {
  const text: string | undefined = ru[key]
  if (text === undefined) {
    console.error(`no text in the catalog for the key ${key}`)
    return key
  }
  return text.replace(/\{(\w+)\}/g, (_, name: string) => String(params[name] ?? ""))
}

/** Text of a key cut at its placeholder `{slot}`: what goes before and after it, so that a screen sets the fact in its own
 *  element (a name in bold) with the words in the order the catalog gives them. */
export function tAround(key: MessageKey, slot: string, params: Record<string, string | number> = {}): [string, string] {
  const marker = "\u0000"
  const [before, after = ""] = t(key, { ...params, [slot]: marker }).split(marker)
  return [before, after]
}

/** Whether the catalog has the text of a key built at run time (a lever registered later has none until it is added). */
export function hasMessage(key: string): key is MessageKey {
  return Object.hasOwn(ru, key)
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
