import { computed, inject, onBeforeUnmount, provide, ref, type InjectionKey } from "vue"

// A window that is reading something says so on its head: a thin bar runs under the title for as long as any tab of it is reading (or
// reading again, with what it had on show). A tab tells it by `useBusyBar`; the window (components/ModalShell.vue) draws the bar. So
// the user has one place to look at to know whether the tables of a window are still coming.
type Register = (busy: () => boolean) => () => void
const WINDOW_BUSY: InjectionKey<Register> = Symbol("the reads of a window")

/** For the window: the bar is on while `busy`; what a tab or a gate inside it reports is gathered by `useBusyBar`. */
export function provideWindowBusy() {
  const flags = ref(new Set<() => boolean>())
  const busy = computed(() => [...flags.value].some((flag) => flag()))
  provide(WINDOW_BUSY, (flag) => {
    flags.value = new Set(flags.value).add(flag)
    return () => {
      const next = new Set(flags.value)
      next.delete(flag)
      flags.value = next
    }
  })
  return busy
}

/** For a tab: its read is under way while `busy()`; out of a window it is nothing. It is let go with the tab. */
export function useBusyBar(busy: () => boolean): void {
  const release = inject(WINDOW_BUSY, null)?.(busy)
  onBeforeUnmount(() => release?.())
}
