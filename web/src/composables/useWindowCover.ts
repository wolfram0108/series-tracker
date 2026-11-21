import { onBeforeUnmount, onMounted, reactive, watch, type Ref } from "vue"

/**
 * The screen under an open window is not seen: a window is opaque. A label of the main screen that lies wholly under the
 * largest open window loses the blur of its backdrop (styles/card.css), which the browser otherwise draws again in every
 * frame for nothing: measured in Chrome with the window «Агенты» open, 89–90 frames a second with it and 116–134 without it.
 * What shows around the window keeps its blur, so the screen looks as before. The cover is smaller than the window by the
 * rounding of its corners.
 */
type Box = { left: number; top: number; right: number; bottom: number }
const boxes = reactive(new Map<number, Box>())
let nextWindow = 0

// The elements of the main screen that blur their backdrop (styles/card.css).
const BLURRED = ".unified-text-bg, .status-pill"

/** A window tells where it stands while it is open. */
export function useWindowBox(room: Ref<HTMLElement | null>): void {
  const id = nextWindow++
  let observer: ResizeObserver | null = null
  const measure = (): void => {
    const element = room.value
    if (!element) return
    const box = element.getBoundingClientRect()
    const round = parseFloat(getComputedStyle(element).borderTopLeftRadius) || 0
    boxes.set(id, { left: box.left + round, top: box.top + round, right: box.right - round, bottom: box.bottom - round })
  }
  onMounted(() => {
    measure()
    if (typeof ResizeObserver !== "undefined" && room.value) {
      observer = new ResizeObserver(measure)
      observer.observe(room.value)
    }
    window.addEventListener("resize", measure)
  })
  onBeforeUnmount(() => {
    observer?.disconnect()
    window.removeEventListener("resize", measure)
    boxes.delete(id)
  })
}

function largest(): Box | null {
  let cover: Box | null = null
  for (const box of boxes.values()) {
    const area = (box.right - box.left) * (box.bottom - box.top)
    if (area > 0 && (!cover || area > (cover.right - cover.left) * (cover.bottom - cover.top))) cover = box
  }
  return cover
}

/** The labels of a part of the main screen that lie wholly under the open window are marked `data-covered` while it is open. */
export function useCoveredBlur(part: Ref<HTMLElement | null>): void {
  let changes: MutationObserver | null = null
  const mark = (): void => {
    const root = part.value
    if (!root) return
    const cover = largest()
    for (const element of root.querySelectorAll<HTMLElement>(BLURRED)) {
      const box = element.getBoundingClientRect()
      const covered = cover !== null && box.width > 0 && box.left >= cover.left && box.right <= cover.right
        && box.top >= cover.top && box.bottom <= cover.bottom
      if (covered !== element.hasAttribute("data-covered")) element.toggleAttribute("data-covered", covered)
    }
  }
  // A window opens, moves or closes: the labels are marked again. While it is open, a label drawn anew (a pill of a status
  // that came) is marked as it appears.
  watch(boxes, () => {
    mark()
    if (largest() && !changes && part.value && typeof MutationObserver !== "undefined") {
      changes = new MutationObserver(mark)
      changes.observe(part.value, { childList: true, subtree: true })
    } else if (!largest()) {
      changes?.disconnect()
      changes = null
    }
  }, { deep: true, flush: "post" })
  onMounted(mark)
  window.addEventListener("resize", mark)
  onBeforeUnmount(() => {
    changes?.disconnect()
    window.removeEventListener("resize", mark)
  })
}
