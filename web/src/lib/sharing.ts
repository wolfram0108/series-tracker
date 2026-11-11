import { computed, type ComputedRef } from "vue"

const plain = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && Object.getPrototypeOf(value) === Object.prototype

/** Whether two values are the same picture: the same object, or plain objects and arrays alike part by part. */
function alike(before: unknown, after: unknown): boolean {
  if (before === after) return true
  if (Array.isArray(before)) {
    if (!Array.isArray(after) || before.length !== after.length) return false
    for (let at = 0; at < after.length; at++) if (!alike(before[at], after[at])) return false
    return true
  }
  if (!plain(before) || !plain(after)) return false
  let count = 0
  for (const key in after) {
    if (!(key in before) || !alike(before[key], after[key])) return false
    count += 1
  }
  for (const _key in before) count -= 1
  return count === 0
}

/**
 * `after` with every part that is the same as in `before` taken from `before`: the same object when nothing differs.
 * Plain objects and arrays are compared part by part (an array by place); anything else is itself. Nothing is built
 * where nothing differs: a picture counted anew and left as it was costs one walk over it.
 */
export function shared<T>(before: unknown, after: T): T {
  if (alike(before, after)) return before as T
  if (Array.isArray(before) && Array.isArray(after)) return after.map((each, at) => shared(before[at], each)) as T
  if (plain(before) && plain(after)) {
    const out: Record<string, unknown> = {}
    for (const key of Object.keys(after)) out[key] = shared(before[key], after[key])
    return out as T
  }
  return after
}

/**
 * What a screen counts from a picture the core pushes whole (a plan, a layout): counted anew with every packet, and every
 * part that came out the same is the object it was. A component shown from an unchanged part is given the same props and
 * is not drawn again: a packet that moves one episode redraws one card, not the season.
 */
export function sharing<T>(count: () => T): ComputedRef<T> {
  let last: T | undefined
  return computed(() => (last = shared(last, count())))
}
