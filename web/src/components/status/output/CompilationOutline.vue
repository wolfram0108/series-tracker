<script setup lang="ts">
import { nextTick, ref, watch } from "vue"
import { outlinePieces, type Geometry, type Piece } from "../../../lib/compilations"

// The outline of the compilations of a season behind its cards or tiles (stage 12): each compilation a band of its colour
// whose outer edge lies on the centre of the gaps and whose arcs are concentric with the corners of the cards. It is drawn
// from the grid as it lies on the page (`layout` grows with every measure of the grid), so it follows any width.
const props = defineProps<{
  grid: HTMLElement | null
  layout: number
  /** Per compilation: the episodes it takes and its colour (1…16). */
  groups: { episodes: number[]; color: number }[]
}>()

interface Drawn { color: number; own: Piece[]; others: Piece[] }
const drawn = ref<Drawn[]>([])
const size = ref({ width: 0, height: 0 })

function draw(): void {
  const grid = props.grid
  const cells = grid ? [...grid.querySelectorAll<HTMLElement>("[data-episode]")] : []
  if (!grid || cells.length === 0 || props.groups.length === 0) {
    drawn.value = []
    return
  }
  const box = grid.getBoundingClientRect()
  const placed = cells.map((cell) => ({ episode: Number(cell.dataset.episode), rect: cell.getBoundingClientRect() }))
  const tops = [...new Set(placed.map((each) => Math.round(each.rect.top)))].sort((a, b) => a - b)
  const lefts = [...new Set(placed.map((each) => Math.round(each.rect.left)))].sort((a, b) => a - b)
  const first = placed[0].rect
  const at = new Map<string, number>()
  for (const each of placed) at.set(`${tops.indexOf(Math.round(each.rect.top))},${lefts.indexOf(Math.round(each.rect.left))}`, each.episode)
  const group = new Map<number, number>()
  props.groups.forEach((each, index) => each.episodes.forEach((episode) => group.set(episode, index)))
  const second = (axis: "left" | "top", line: number) => placed.find((each) => Math.round(each.rect[axis]) === line)?.rect
  const style = getComputedStyle(grid)
  const gapX = parseFloat(style.columnGap) || 0
  const gapY = parseFloat(style.rowGap) || 0
  const geometry: Geometry = {
    x0: first.left - box.left, y0: first.top - box.top,
    pitchX: lefts.length > 1 ? second("left", lefts[1])!.left - first.left : first.width + gapX,
    pitchY: tops.length > 1 ? second("top", tops[1])!.top - first.top : first.height + gapY,
    w: first.width, h: first.height,
    radius: Math.min(parseFloat(getComputedStyle(cells[0]).borderTopLeftRadius) || 0, first.width / 2, first.height / 2),
    rows: tops.length, cols: lefts.length,
  }
  const pieces = outlinePieces(geometry, (row, col) => {
    const episode = at.get(`${row},${col}`)
    return episode === undefined ? -1 : group.get(episode) ?? -1
  })
  size.value = { width: box.width, height: grid.scrollHeight }
  drawn.value = props.groups.map((each, index) => {
    const own = pieces.filter((piece) => piece.owner === index)
    const rows = own.map((piece) => piece.row)
    const cols = own.map((piece) => piece.col)
    const near = (piece: Piece) => piece.row >= Math.min(...rows) - 1 && piece.row <= Math.max(...rows) + 1
      && piece.col >= Math.min(...cols) - 1 && piece.col <= Math.max(...cols) + 1
    return { color: each.color, own, others: pieces.filter((piece) => piece.owner !== index && near(piece)) }
  }).filter((each) => each.own.length > 0)
}

watch(() => [props.grid, props.layout, props.groups], () => void nextTick(draw), { deep: true, immediate: true })
const id = `outline-${Math.random().toString(36).slice(2)}`
</script>

<template>
  <svg v-if="drawn.length" class="compilation-outline" :width="size.width" :height="size.height" aria-hidden="true">
    <defs>
      <mask v-for="(each, index) in drawn" :id="`${id}-${index}`" :key="index" maskUnits="userSpaceOnUse"
            x="-50" y="-50" :width="size.width + 100" :height="size.height + 100">
        <rect x="-50" y="-50" :width="size.width + 100" :height="size.height + 100" fill="#000" />
        <path v-for="(piece, at) in each.own" :key="at" :d="piece.d" fill="#fff" />
      </mask>
    </defs>
    <g v-for="(each, index) in drawn" :key="index" :style="{ '--tone': `var(--compilation-${each.color})` }">
      <path v-for="(piece, at) in each.own" :key="`g${at}`" :d="piece.d" class="compilation-ground" />
      <g :mask="`url(#${id}-${index})`" class="compilation-ring">
        <path v-for="(piece, at) in each.others" :key="`r${at}`" :d="piece.d" />
      </g>
    </g>
  </svg>
</template>

<style scoped>
.compilation-outline { position: absolute; inset-block-start: 0; inset-inline-start: 0; z-index: -1; overflow: visible; pointer-events: none; }
.compilation-ground { fill: var(--tone); fill-opacity: var(--compilation-ground-share); }
/* What is not the compilation, grown by the width of the line and seen only inside it: the band along its edge. */
.compilation-ring { fill: var(--tone); stroke: var(--tone); stroke-width: calc(2 * var(--compilation-line)); stroke-linejoin: round; }
</style>
