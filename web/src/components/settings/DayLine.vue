<script setup lang="ts">
import { computed, ref } from "vue"
import { t } from "../../i18n"
import { STEP, hhmm, minuteAt, minuteOf, placeOf, type PointKind } from "../../lib/schedule"

// The day on one line, 00:00 to 24:00: the syncs (blue squares) and the checks of the torrent releases (orange circles),
// each with its time above it. A point is moved by dragging it or by the arrow keys; the checks the core places between
// the syncs stand still. A point picked shows «×» by its time.
const props = defineProps<{ syncs: string[]; checks: string[]; checksMove: boolean; picked: { kind: PointKind; at: number } | null }>()
const emit = defineEmits<{
  move: [kind: PointKind, at: number, time: string]; pick: [kind: PointKind, at: number]; remove: [kind: PointKind, at: number]
}>()

const line = ref<HTMLElement | null>(null)
// The point being dragged, with the time it shows until it is let go.
const dragged = ref<{ kind: PointKind; at: number; time: string } | null>(null)
const shown = (kind: PointKind, at: number, time: string): string =>
  dragged.value && dragged.value.kind === kind && dragged.value.at === at ? dragged.value.time : time
const points = computed(() => [
  ...props.syncs.map((time, at) => ({ kind: "sync" as const, at, time: shown("sync", at, time) })),
  ...props.checks.map((time, at) => ({ kind: "check" as const, at, time: shown("check", at, time) })),
])
const movable = (kind: PointKind): boolean => kind === "sync" || props.checksMove
const isPicked = (kind: PointKind, at: number): boolean => props.picked?.kind === kind && props.picked.at === at
const TICKS = [0, 4, 8, 12, 16, 20]

function timeAt(event: PointerEvent): string {
  const box = line.value!.getBoundingClientRect()
  return hhmm(minuteAt((event.clientX - box.left) / box.width))
}
function grab(event: PointerEvent, kind: PointKind, at: number, time: string): void {
  emit("pick", kind, at)
  if (!movable(kind)) return
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
  dragged.value = { kind, at, time }
}
function drag(event: PointerEvent): void {
  if (dragged.value) dragged.value = { ...dragged.value, time: timeAt(event) }
}
function drop(): void {
  const done = dragged.value
  dragged.value = null
  if (done) emit("move", done.kind, done.at, done.time)
}
function step(event: KeyboardEvent, kind: PointKind, at: number, time: string): void {
  const by = event.key === "ArrowRight" ? STEP : event.key === "ArrowLeft" ? -STEP : 0
  if (event.key === "Delete" || event.key === "Backspace") emit("remove", kind, at)
  if (!by || !movable(kind)) return
  event.preventDefault()
  emit("move", kind, at, hhmm((minuteOf(time) ?? 0) + by))
}
</script>

<template>
  <div class="day-line">
    <div class="day-labels is-sync" aria-hidden="true">
      <span v-for="each in points.filter((p) => p.kind === 'sync')" :key="`s${each.at}`" class="day-label"
            :style="{ insetInlineStart: `${placeOf(each.time)}%` }">{{ each.time }}</span>
    </div>
    <div class="day-labels is-check">
      <span v-for="each in points.filter((p) => p.kind === 'check')" :key="`c${each.at}`" class="day-label"
            :class="{ 'is-picked': isPicked('check', each.at) }" :style="{ insetInlineStart: `${placeOf(each.time)}%` }">
        {{ each.time }}
        <button v-if="isPicked('check', each.at) && checksMove" type="button" class="day-remove"
                :aria-label="t('schedule.remove', { at: each.time })" @click="emit('remove', 'check', each.at)">
          <i class="pi pi-times" aria-hidden="true" />
        </button>
      </span>
    </div>
    <div ref="line" class="day-track" @pointermove="drag" @pointerup="drop" @pointercancel="drop">
      <span class="day-bar" />
      <button v-for="each in points" :key="`${each.kind}${each.at}`" type="button" class="day-point"
              :class="[`is-${each.kind}`, { 'is-picked': isPicked(each.kind, each.at), 'is-fixed': !movable(each.kind) }]"
              :style="{ insetInlineStart: `${placeOf(each.time)}%` }"
              :aria-label="t(each.kind === 'sync' ? 'schedule.syncPoint' : 'schedule.checkPoint', { at: each.time })"
              @pointerdown="grab($event, each.kind, each.at, each.time)" @keydown="step($event, each.kind, each.at, each.time)" />
    </div>
    <div class="day-ticks" aria-hidden="true">
      <span v-for="hour in TICKS" :key="hour" :style="{ insetInlineStart: `${(hour / 24) * 100}%` }">{{ hhmm(hour * 60) }}</span>
      <span class="is-end">24:00</span>
    </div>
  </div>
</template>

<style scoped>
.day-line { display: flex; flex-direction: column; gap: 0.25rem; padding: 0.9rem 1.5rem 0.75rem; background: var(--bg-light);
  border: 1px solid var(--border-color); border-radius: var(--border-radius); }
.day-labels, .day-ticks { position: relative; block-size: 1.2rem; }
.day-label { position: absolute; transform: translateX(-50%); font-size: 0.8rem; font-weight: 700; white-space: nowrap;
  display: inline-flex; align-items: center; gap: 0.2rem; }
.is-sync .day-label { color: var(--color-blue-ink); }
.is-check .day-label { color: var(--fresh-stale-ink); }
.day-label.is-picked { padding-inline: 0.4rem 0.15rem; border-radius: 0.35rem; background: var(--bg-white); box-shadow: var(--card-shadow); }
.day-remove { border: 0; background: transparent; color: inherit; padding: 0.1rem; cursor: pointer; line-height: 1; }
.day-remove .pi { font-size: 0.7rem; }
.day-track { position: relative; block-size: 1.9rem; touch-action: none; }
.day-bar { position: absolute; inset-inline: 0; inset-block-start: 50%; block-size: 0.35rem; transform: translateY(-50%);
  border-radius: 0.2rem; background: var(--border-color); }
.day-point { position: absolute; inset-block-start: 50%; transform: translate(-50%, -50%); padding: 0; cursor: grab;
  border: 0.125rem solid var(--bg-white); box-shadow: var(--card-shadow); }
.day-point.is-sync { inline-size: 1.4rem; block-size: 1.4rem; border-radius: 0.35rem; background: var(--color-blue); }
.day-point.is-check { inline-size: 1.1rem; block-size: 1.1rem; border-radius: 50%; background: var(--check-color); }
.day-point.is-fixed { cursor: default; }
.day-point.is-picked { box-shadow: 0 0 0 0.2rem var(--color-yellow-2); }
.day-ticks span { position: absolute; transform: translateX(-50%); font-size: 0.7rem; color: var(--text-muted); }
.day-ticks span:first-child { transform: none; }
.day-ticks .is-end { inset-inline-end: 0; transform: none; }
</style>
