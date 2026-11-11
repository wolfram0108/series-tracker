import { markRaw, shallowReactive } from "vue"

/**
 * A picture the core pushes by key (a series, a page), each key replaced whole by its packet. A write wakes only what
 * read that key, and what listed the keys when one appears or goes: a store that replaces its whole map on every packet
 * wakes every reader of every key, so a window of one series recounts itself on the news of another.
 *
 * A packet is kept as it came and is the same object wherever it is read or stored (a screen tells the news it has
 * handled from newer news by identity): nothing wraps it in a proxy.
 */
export function keyed<Value>() {
  const all = shallowReactive<Record<string | number, Value>>({})

  function put(key: string | number, value: Value): void {
    all[key] = typeof value === "object" && value !== null ? markRaw(value) : value
  }

  function drop(key: string | number): void {
    delete all[key]
  }

  function clear(): void {
    for (const key of Object.keys(all)) delete all[key]
  }

  return { all, put, drop, clear }
}
