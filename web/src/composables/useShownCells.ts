import { onBeforeUnmount, onMounted, ref, type Ref } from "vue"

/**
 * The cards of a grid built only when they come near the visible part of the area that scrolls them: a season of hundreds
 * of episodes builds the rows on screen and the next screen, not every card at once (its first show froze the page for
 * seconds). Until then a card is a shell of its size (`shell` is its element ref), which the outline of the grid and the
 * selection count as the card. A card once built stays built. Where the browser cannot tell what is near the view (no
 * IntersectionObserver), every card is built.
 */
export function useShownCells(grid: Ref<HTMLElement | null>) {
  const every = typeof IntersectionObserver === "undefined"
  const seen = ref<ReadonlySet<number>>(new Set())
  const shells = new Map<number, Element>()
  const episodeOf = new Map<Element, number>()
  let observer: IntersectionObserver | null = null

  function scrollerOf(from: HTMLElement): HTMLElement | null {
    for (let node = from.parentElement; node; node = node.parentElement) {
      const overflow = getComputedStyle(node).overflowY
      if (overflow === "auto" || overflow === "scroll") return node
    }
    return null
  }

  function onSeen(entries: IntersectionObserverEntry[]): void {
    const near = entries.filter((entry) => entry.isIntersecting).map((entry) => episodeOf.get(entry.target))
      .filter((episode): episode is number => episode !== undefined)
    if (near.length === 0) return
    seen.value = new Set([...seen.value, ...near])
    for (const episode of near) forget(episode)
  }

  function forget(episode: number): void {
    const element = shells.get(episode)
    if (!element) return
    observer?.unobserve(element)
    shells.delete(episode)
    episodeOf.delete(element)
  }

  onMounted(() => {
    if (every || !grid.value) return
    // A screen above and below the view: the rows a scroll brings next are built before they show.
    observer = new IntersectionObserver(onSeen, { root: scrollerOf(grid.value), rootMargin: "100% 0px" })
    for (const element of shells.values()) observer.observe(element)
  })
  onBeforeUnmount(() => observer?.disconnect())

  /** Whether the card of the episode is built. */
  const built = (episode: number): boolean => every || seen.value.has(episode)

  /** The element ref of the shell of an episode: watched until the card comes near the view. */
  const shell = (episode: number) => (element: unknown): void => {
    if (!(element instanceof Element)) {
      forget(episode)
      return
    }
    if (shells.get(episode) === element) return
    forget(episode)
    shells.set(episode, element)
    episodeOf.set(element, episode)
    observer?.observe(element)
  }

  return { built, shell }
}
