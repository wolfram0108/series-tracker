// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { defineComponent, h, nextTick, ref } from "vue"
import { mount } from "@vue/test-utils"
import { useShownCells } from "./useShownCells"

// The observer of the browser as a test drives it: what it watches, and the news it gives when told.
class Watcher {
  static last: Watcher | null = null
  watched = new Set<Element>()
  constructor(public tell: (entries: IntersectionObserverEntry[]) => void, public options: IntersectionObserverInit) {
    Watcher.last = this
  }
  observe(element: Element) { this.watched.add(element) }
  unobserve(element: Element) { this.watched.delete(element) }
  disconnect() { this.watched.clear() }
  near(...elements: Element[]) {
    this.tell(elements.map((target) => ({ target, isIntersecting: true }) as IntersectionObserverEntry))
  }
  far(element: Element, height: number) {
    this.tell([{ target: element, isIntersecting: false, boundingClientRect: { height } } as IntersectionObserverEntry])
  }
}

// A grid in a scrolling area: a drawn card is a <b>, a shell an <i>; both are watched by their ref.
const Grid = defineComponent({
  props: { episodes: { type: Array as () => number[], required: true } },
  setup(props) {
    const grid = ref<HTMLElement | null>(null)
    const { built, cell, sizeOf } = useShownCells(grid)
    return () => h("div", { style: "overflow-y: auto" }, [h("div", { ref: grid }, props.episodes.map((episode) => built(episode)
      ? h("b", { key: `b${episode}`, "data-episode": episode, ref: cell(episode) })
      : h("i", { key: `i${episode}`, "data-episode": episode, ref: cell(episode), style: sizeOf(episode) })))])
  },
})
const kinds = (w: ReturnType<typeof mount>) => w.findAll("[data-episode]").map((each) => each.element.tagName.toLowerCase())
const at = (w: ReturnType<typeof mount>, episode: number) => w.find(`[data-episode='${episode}']`).element

afterEach(() => {
  vi.unstubAllGlobals()
  Watcher.last = null
})

describe("useShownCells", () => {
  it("draws a card near the view and takes it down when it goes far, its shell keeping the card's height", async () => {
    vi.stubGlobal("IntersectionObserver", Watcher)
    const w = mount(Grid, { props: { episodes: [1, 2, 3, 4] }, attachTo: document.body })
    await nextTick()
    expect(kinds(w)).toEqual(["i", "i", "i", "i"])
    const watcher = Watcher.last!
    expect(watcher.options.root).toBe(w.element)  // the area that scrolls the grid
    watcher.near(at(w, 1), at(w, 2))
    await nextTick()
    expect(kinds(w)).toEqual(["b", "b", "i", "i"])
    expect(watcher.watched.size).toBe(4)  // a drawn card is watched too: it is taken down when it goes far
    watcher.far(at(w, 1), 132)
    await nextTick()
    expect(kinds(w)).toEqual(["i", "b", "i", "i"])
    expect((at(w, 1) as HTMLElement).style.blockSize).toBe("132px")
    expect(watcher.watched.size).toBe(4)
    w.unmount()
  })

  it("stops watching a card that went from the list", async () => {
    vi.stubGlobal("IntersectionObserver", Watcher)
    const w = mount(Grid, { props: { episodes: [1, 2, 3] }, attachTo: document.body })
    await nextTick()
    Watcher.last!.near(at(w, 1))
    await nextTick()
    await w.setProps({ episodes: [1, 3] })
    expect(kinds(w)).toEqual(["b", "i"])
    expect(Watcher.last!.watched.size).toBe(2)
    w.unmount()
  })

  it("draws every card where the browser cannot tell what is near the view", async () => {
    vi.stubGlobal("IntersectionObserver", undefined)
    const w = mount(Grid, { props: { episodes: [1, 2, 3] } })
    await nextTick()
    expect(kinds(w)).toEqual(["b", "b", "b"])
  })
})
