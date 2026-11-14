import { nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from "vue"
import { computeDropStyle, type DropStyle } from "./useDropAnchor"
import { useLayer } from "./useLayers"

// What every list that drops from a trigger does: opens under it, stays where the trigger is (useDropAnchor), closes on a press
// outside, on Escape (a layer: the window under it stays) and on Tab, and is worked from the keyboard as a listbox whose focus
// stays on the trigger (aria-activedescendant): the arrows walk the options, Home and End go to the ends, Enter and Space take
// one. The trigger carries role, tabindex and the `aria-*` of `triggerAttrs`; the list is teleported to <body> by its component.
export interface DropListOptions {
  count: () => number
  /** The index of the value the trigger holds, or -1: the walk begins there. */
  selected: () => number
  choose: (index: number) => void
  place?: () => Parameters<typeof computeDropStyle>[1]
  /** Run before every opening, so that the list is as the core has it now. */
  before?: () => Promise<void>
}

export function useDropList(options: DropListOptions) {
  const open = ref(false)
  const root = ref<HTMLElement | null>(null)
  const list = ref<HTMLElement | null>(null)
  const dropStyle = ref<DropStyle | null>(null)
  const active = ref(-1)
  const listId = useId()
  const optionId = (index: number): string => `${listId}-${index}`
  const layer = useLayer(() => close())

  function reposition(): void {
    if (root.value) dropStyle.value = computeDropStyle(root.value, options.place?.())
  }
  async function show(): Promise<void> {
    await options.before?.()
    reposition() // the place before the opening: the list appears where it stays
    active.value = options.selected()
    open.value = true
    layer.enter()
  }
  function close(): void {
    if (!open.value) return
    open.value = false
    active.value = -1
    layer.leave()
  }
  function toggle(): void {
    if (open.value) close()
    else void show()
  }
  function take(index: number): void {
    options.choose(index)
    close()
  }
  function walk(to: number): void {
    const last = options.count() - 1
    if (last >= 0) active.value = Math.min(last, Math.max(0, to))
  }
  function onKey(event: KeyboardEvent): void {
    if (event.altKey || event.ctrlKey || event.metaKey) return
    switch (event.key) {
      case "ArrowDown":
      case "ArrowUp":
        event.preventDefault()
        if (!open.value) void show()
        else walk(active.value + (event.key === "ArrowDown" ? 1 : -1))
        break
      case "Home":
      case "End":
        if (!open.value) return
        event.preventDefault()
        walk(event.key === "Home" ? 0 : options.count() - 1)
        break
      case "Enter":
      case " ":
        event.preventDefault()
        if (!open.value) void show()
        else if (active.value >= 0 && active.value < options.count()) take(active.value)
        else close()
        break
      case "Tab":
        close() // the focus leaves the trigger: the list does not stay open behind it
        break
    }
  }

  const onOutside = (event: MouseEvent): void => {
    const target = event.target as Node
    if (open.value && !root.value?.contains(target) && !list.value?.contains(target)) close()
  }
  const onMove = (): void => {
    if (open.value) reposition()
  }
  onMounted(() => {
    document.addEventListener("click", onOutside, true)
    window.addEventListener("scroll", onMove, true)
    window.addEventListener("resize", onMove)
  })
  onBeforeUnmount(() => {
    document.removeEventListener("click", onOutside, true)
    window.removeEventListener("scroll", onMove, true)
    window.removeEventListener("resize", onMove)
  })

  // The option walked to is kept in sight of a list that scrolls.
  watch(active, (index) => {
    if (index >= 0) void nextTick(() => document.getElementById(optionId(index))?.scrollIntoView?.({ block: "nearest" }))
  })

  /** The attributes of the trigger that tell its list to the reader of the screen. */
  const triggerAttrs = (): Record<string, unknown> => ({
    tabindex: 0,
    "aria-haspopup": "listbox",
    "aria-expanded": open.value,
    "aria-controls": open.value ? listId : undefined,
    "aria-activedescendant": open.value && active.value >= 0 ? optionId(active.value) : undefined,
  })

  return { open, root, list, dropStyle, active, listId, optionId, toggle, take, close, onKey, triggerAttrs }
}
