import { computed, ref } from "vue"

// An action that goes to the core and takes its time is done once for one press: a second press (a double click, a button pressed
// again by a user who sees nothing happen on a slow line) while the first is not answered is not a second action. `keyOf` makes the
// action one per thing it is done to (a series), so that the sync of one does not hold up the sync of another.
export function useAction<A extends unknown[]>(act: (...args: A) => Promise<unknown>, keyOf: (...args: A) => string = () => "") {
  const running = ref(new Set<string>())

  async function run(...args: A): Promise<void> {
    const key = keyOf(...args)
    if (running.value.has(key)) return
    running.value = new Set(running.value).add(key)
    try {
      await act(...args)
    } finally {
      const next = new Set(running.value)
      next.delete(key)
      running.value = next
    }
  }

  /** Whether the action is under way (for the thing named by `key`, for any when none is named): the button shows it. */
  const pending = (key?: string): boolean => (key === undefined ? running.value.size > 0 : running.value.has(key))
  const any = computed(() => running.value.size > 0)
  return { run, pending, any }
}
