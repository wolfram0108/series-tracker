// The pick of the editors of the tab «Торрент»: the items chosen on the left and their drag to a place on the right. A
// drop target is a button as well, so that the keyboard places the chosen items with no drag at all.
import { computed, ref } from "vue"

export function useTorrentPick() {
  const chosen = ref(new Set<string>())
  const dragging = ref(false)
  const count = computed(() => chosen.value.size)

  const has = (item: string): boolean => chosen.value.has(item)
  function toggle(item: string): void {
    const next = new Set(chosen.value)
    if (!next.delete(item)) next.add(item)
    chosen.value = next
  }
  /** All of `items` chosen, or none of them when all already are. */
  function toggleAll(items: readonly string[]): void {
    const all = items.length > 0 && items.every((item) => chosen.value.has(item))
    const next = new Set(chosen.value)
    for (const item of items) all ? next.delete(item) : next.add(item)
    chosen.value = next
  }
  const allOf = (items: readonly string[]): boolean => items.length > 0 && items.every((item) => chosen.value.has(item))
  const someOf = (items: readonly string[]): boolean => !allOf(items) && items.some((item) => chosen.value.has(item))
  function choose(items: readonly string[]): void {
    chosen.value = new Set(items)
  }
  function clear(): void {
    chosen.value = new Set()
  }

  /** A drag begun on items not chosen takes those items alone. */
  function dragStart(event: DragEvent, items: readonly string[]): void {
    if (!items.some((item) => chosen.value.has(item))) choose(items)
    event.dataTransfer?.setData("text/plain", [...chosen.value].join("\n"))
    if (event.dataTransfer) event.dataTransfer.effectAllowed = "move"
    dragging.value = true
  }
  function dragEnd(): void {
    dragging.value = false
  }

  /** The handlers of a drop target: what it does with the chosen items once they are let go on it. */
  function target(apply: (items: string[]) => void) {
    return {
      onDragover: (event: DragEvent) => { if (dragging.value) event.preventDefault() },
      onDrop: (event: DragEvent) => {
        event.preventDefault()
        dragging.value = false
        if (chosen.value.size) apply([...chosen.value])
      },
    }
  }

  return { chosen, count, dragging, has, toggle, toggleAll, allOf, someOf, choose, clear, dragStart, dragEnd, target }
}

export type TorrentPick = ReturnType<typeof useTorrentPick>
