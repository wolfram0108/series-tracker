import { computed, onBeforeUnmount, onMounted, ref, watch, type Ref } from "vue"
import { perRowOf } from "../lib/output"

/**
 * The grid of the cards of a block (a season, a compilation): how many of the smallest cards it has room for, measured on
 * the grid itself (`ruler` is a hidden element of the width of the smallest card), and how many a row holds for the tracks
 * and the episodes of the block (lib/output.ts: perRowOf). `layout` grows with every measure, so that what is drawn over the
 * grid (the outline of compilations) is drawn again when the grid moves.
 */
export function useCardGrid(mode: () => "cards" | "tiles", tracks: () => number, total: () => number) {
  const grid: Ref<HTMLElement | null> = ref(null)
  const ruler: Ref<HTMLElement | null> = ref(null)
  const places = ref(1)
  const layout = ref(0)
  let observer: ResizeObserver | null = null

  function measure(): void {
    if (!grid.value || !ruler.value) return
    const unit = ruler.value.getBoundingClientRect().width
    const gap = parseFloat(getComputedStyle(grid.value).columnGap) || 0
    const width = grid.value.getBoundingClientRect().width
    places.value = unit > 0 ? Math.max(1, Math.floor((width + gap) / (unit + gap))) : 1
    layout.value += 1
  }
  onMounted(() => {
    measure()
    if (typeof ResizeObserver !== "undefined" && grid.value) {
      observer = new ResizeObserver(measure)
      observer.observe(grid.value)
    }
  })
  onBeforeUnmount(() => observer?.disconnect())
  watch(mode, measure, { flush: "post" })

  const perRow = computed(() => perRowOf(places.value, tracks(), total()))
  const wide = computed(() => perRow.value < Math.min(places.value, 6))
  const columns = computed(() => mode() === "cards" ? { gridTemplateColumns: `repeat(${perRow.value}, minmax(0, 1fr))` } : {})
  return { grid, ruler, places, perRow, wide, columns, layout }
}
