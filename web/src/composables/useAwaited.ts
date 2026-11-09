import { computed, inject, shallowRef, watch, type ComputedRef } from "vue"
import { CHANGE_ANSWER } from "../lib/changeAnswer"

/**
 * A value the core holds, shown as the user changed it while the core has not answered: an order dragged, a name typed.
 * A value the core accepted stays until the core's value comes back; a refused one goes back to the value the core holds;
 * nothing jumps back meanwhile. Another value of the core ends the wait at once; the same value made again (another field
 * changed) does not, unless the core has already accepted. The answers come from the window (CHANGE_ANSWER).
 */
export function useAwaited<T>(core: () => T, keyOf: (value: T) => string): {
  shown: ComputedRef<T>
  hold: (value: T) => void
} {
  const pending = shallowRef<{ value: T; before: string; accepted: boolean } | null>(null)
  const shown = computed<T>(() => (pending.value ? pending.value.value : core()))

  watch(core, (value) => {
    const wait = pending.value
    if (wait && (wait.accepted || keyOf(value) !== wait.before)) pending.value = null
  })

  const answer = inject(CHANGE_ANSWER, null)
  if (answer) {
    watch(answer, (next) => {
      const wait = pending.value
      if (wait) pending.value = next.ok ? { ...wait, accepted: true } : null
    })
  }

  // The value sent to the core, shown until it answers.
  const hold = (value: T): void => {
    pending.value = { value, before: keyOf(core()), accepted: false }
  }
  return { shown, hold }
}
