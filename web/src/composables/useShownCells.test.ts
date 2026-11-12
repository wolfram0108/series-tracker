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
}

// A grid in a scrolling area: a built card is a <b>, a shell an <i> watched by its ref.
const Grid = defineComponent({
  props: { episodes: { type: Array as () => number[], required: true } },
  setup(props) {
    const grid = ref<HTMLElement | null>(null)
    const { built, shell } = useShownCells(grid)
    return () => h("div", { style: "overflow-y: auto" }, [h("div", { ref: grid }, props.episodes.map((episode) => built(episode)
      ? h("b", { key: `b${episode}`, "data-episode": episode })
      : h("i", { key: `i${episode}`, "data-episode": episode, ref: shell(episode) })))])
  },
})
const kinds = (w: ReturnType<typeof mount>) => w.findAll("[data-episode]").map((each) => each.element.tagName.toLowerCase())

afterEach(() => {
  vi.unstubAllGlobals()
  Watcher.last = null
})

describe("useShownCells", () => {
  it("builds a card when its shell comes near the view, and leaves the others shells", async () => {
    vi.stubGlobal("IntersectionObserver", Watcher)
    const w = mount(Grid, { props: { episodes: [1, 2, 3, 4] }, attachTo: document.body })
    await nextTick()
    expect(kinds(w)).toEqual(["i", "i", "i", "i"])
    const watcher = Watcher.last!
    expect(watcher.watched.size).toBe(4)
    expect(watcher.options.root).toBe(w.element)  // the area that scrolls the grid
    watcher.near(w.find("[data-episode='1']").element, w.find("[data-episode='2']").element)
    await nextTick()
    expect(kinds(w)).toEqual(["b", "b", "i", "i"])
    expect(watcher.watched.size).toBe(2)  // a built card is watched no more
    w.unmount()
  })

  it("keeps a card built when the episodes shown change, and stops watching a shell that went", async () => {
    vi.stubGlobal("IntersectionObserver", Watcher)
    const w = mount(Grid, { props: { episodes: [1, 2, 3] }, attachTo: document.body })
    await nextTick()
    Watcher.last!.near(w.find("[data-episode='1']").element)
    await nextTick()
    await w.setProps({ episodes: [1, 3] })
    expect(kinds(w)).toEqual(["b", "i"])
    expect(Watcher.last!.watched.size).toBe(1)
    w.unmount()
  })

  it("builds every card where the browser cannot tell what is near the view", async () => {
    vi.stubGlobal("IntersectionObserver", undefined)
    const w = mount(Grid, { props: { episodes: [1, 2, 3] } })
    await nextTick()
    expect(kinds(w)).toEqual(["b", "b", "b"])
  })
})
