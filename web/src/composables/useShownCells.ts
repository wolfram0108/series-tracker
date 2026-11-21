import { onBeforeUnmount, onMounted, reactive, type Ref } from "vue"

type Key = string | number

/**
 * The cards of a list or a grid drawn only while they are near the visible part of the area that scrolls them: a list of
 * hundreds of cards draws the ones on screen and a screen above and below, not every card at once. A card that goes
 * further away is taken down, and a shell of the size it had stands in its place, so that the scroll, the grid and what
 * counts the cards (the outline of a grid, the selection) stand as they did; the card is drawn again when it comes near.
 * A card that holds the focus stays drawn. Where the browser cannot tell what is near the view (no IntersectionObserver),
 * every card is drawn.
 *
 * A list renders each card in a ShownCell (components/ShownCell.vue): the card, or its shell sized by `sizeOf(key)`, both with
 * `:ref="cell(key)"`.
 */
export type ShownCells = ReturnType<typeof useShownCells>

export function useShownCells(area: Ref<HTMLElement | null>) {
  const every = typeof IntersectionObserver === "undefined"
  const near = reactive(new Set<Key>())
  // The height a card had when it was taken down: its shell keeps it.
  const heights = reactive(new Map<Key, number>())
  const cells = new Map<Key, Element>()
  const keyOf = new Map<Element, Key>()
  let observer: IntersectionObserver | null = null

  function scrollerOf(from: HTMLElement): HTMLElement | null {
    for (let node = from.parentElement; node; node = node.parentElement) {
      const overflow = getComputedStyle(node).overflowY
      if (overflow === "auto" || overflow === "scroll") return node
    }
    return null
  }

  function onSight(entries: IntersectionObserverEntry[]): void {
    for (const entry of entries) {
      const key = keyOf.get(entry.target)
      if (key === undefined) continue
      if (entry.isIntersecting) near.add(key)
      else if (near.has(key) && !entry.target.contains(document.activeElement)) {
        if (entry.boundingClientRect.height > 0) heights.set(key, entry.boundingClientRect.height)
        near.delete(key)
      }
    }
  }

  onMounted(() => {
    if (every || !area.value) return
    // A screen above and below the view: the cards a scroll brings next are drawn before they show.
    observer = new IntersectionObserver(onSight, { root: scrollerOf(area.value), rootMargin: "100% 0px" })
    for (const element of cells.values()) observer.observe(element)
  })
  onBeforeUnmount(() => observer?.disconnect())

  /** Whether the card of the key is drawn now. */
  const built = (key: Key): boolean => every || near.has(key)

  /** The element ref of the card of a key or of its shell: whichever stands there is watched. The ref of a key is one function
   *  for the life of the list: a new one at every render would make the list unwatch and watch again every cell it holds. */
  const refs = new Map<Key, (ref: unknown) => void>()
  const cell = (key: Key): ((ref: unknown) => void) => {
    let made = refs.get(key)
    if (!made) {
      made = (ref: unknown) => watchCell(key, ref)
      refs.set(key, made)
    }
    return made
  }
  const forget = (key: Key, element: Element): void => {
    observer?.unobserve(element)
    keyOf.delete(element)
    if (cells.get(key) === element) cells.delete(key)
  }
  const watchCell = (key: Key, ref: unknown): void => {
    const element = ref instanceof Element ? ref : (ref as { $el?: unknown } | null)?.$el
    const old = cells.get(key)
    if (element instanceof Element) {
      if (old === element) return
      if (old) forget(key, old)
      cells.set(key, element)
      keyOf.set(element, key)
      observer?.observe(element)
      return
    }
    // A cell went: the card that took its place may have come before this news, so what is gone is told once the render
    // is over, by the element no longer being in the page. A key whose cell is gone for good is forgotten.
    queueMicrotask(() => {
      const now = cells.get(key)
      if (now && !now.isConnected) {
        forget(key, now)
        refs.delete(key)
      }
    })
  }

  /** The size of the shell of a key: the height its card had, or the shell's own (its CSS) before the card was ever drawn. */
  const sizeOf = (key: Key): Record<string, string> | undefined => {
    const height = heights.get(key)
    return height === undefined ? undefined : { blockSize: `${height}px` }
  }

  return { built, cell, sizeOf }
}
