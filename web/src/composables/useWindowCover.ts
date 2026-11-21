import { computed, onBeforeUnmount, onMounted, reactive, ref, watch, type Ref } from "vue"

/**
 * The screen under an open window is not seen: a window is opaque. The parts of the main screen (its header, the list of the
 * series) are cut where the largest open window stands, so the browser does not draw them there in every frame (their
 * stripes, pulses and the blur under their labels, which a window over them made the browser draw for nothing). The cut is
 * smaller than the window by the rounding of its corners, so whatever shows around the window shows as before.
 */
const boxes = reactive(new Map<number, { left: number; top: number; right: number; bottom: number }>())
let nextWindow = 0

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

/** The style of a part of the main screen: cut where the largest open window covers it, nothing while no window is open. */
export function useUnderWindows(part: Ref<HTMLElement | null>) {
  const place = ref<{ left: number; top: number } | null>(null)
  let observer: ResizeObserver | null = null
  const measure = (): void => {
    const box = part.value?.getBoundingClientRect()
    place.value = box ? { left: box.left, top: box.top } : null
  }
  onMounted(() => {
    measure()
    if (typeof ResizeObserver !== "undefined" && part.value) {
      observer = new ResizeObserver(measure)
      observer.observe(part.value)
    }
    window.addEventListener("resize", measure)
    window.addEventListener("scroll", measure, { passive: true })
  })
  // A window that opens or moves may move the part too (the lock of the scroll): the part is measured again after it.
  watch(boxes, measure, { deep: true, flush: "post" })
  onBeforeUnmount(() => {
    observer?.disconnect()
    window.removeEventListener("resize", measure)
    window.removeEventListener("scroll", measure)
  })
  return computed<Record<string, string> | undefined>(() => {
    let cover: { left: number; top: number; right: number; bottom: number } | null = null
    for (const box of boxes.values()) {
      const area = (box.right - box.left) * (box.bottom - box.top)
      if (area > 0 && (!cover || area > (cover.right - cover.left) * (cover.bottom - cover.top))) cover = box
    }
    if (!cover || !place.value) return undefined
    const x0 = cover.left - place.value.left, y0 = cover.top - place.value.top
    const x1 = cover.right - place.value.left, y1 = cover.bottom - place.value.top
    // The outer edge lies far outside the part: what it draws beyond its box (the shadows of the cards) stays as it was.
    const far = "9999px"
    const outer = `-${far} -${far}, calc(100% + ${far}) -${far}, calc(100% + ${far}) calc(100% + ${far}), -${far} calc(100% + ${far}), -${far} -${far}`
    return { clipPath: `polygon(evenodd, ${outer}, ${x0}px ${y0}px, ${x1}px ${y0}px, ${x1}px ${y1}px, ${x0}px ${y1}px, ${x0}px ${y0}px)` }
  })
}
