<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue"
import type { Series } from "../stores/series"
import { useSeriesReadsStore } from "../stores/seriesReads"
import { progressText } from "../lib/sources"
import { t, type MessageKey } from "../i18n"

// Карточка сериала — кастом-островок. Порт логики из app.js
// (getLayerStyle/getAnimationClass/seriesWithPills) на реальную модель серии.
const props = defineProps<{ series: Series; saving?: boolean }>()
const emit = defineEmits<{
  (e: "open-status", id: number): void
  (e: "scan", id: number): void
  (e: "delete", id: number): void
  (e: "toggle-auto-scan", id: number, enabled: boolean): void
}>()

const layerHierarchy = [
  "waiting", "ready", "downloading", "queued", "slicing", "aligning", "packing", "activating",
  "checking", "renaming", "metadata", "scanning", "viewing", "error",
]
// The words of a status are `series.status.<name>`; `scanning` is an open run of a sync, shown as processing.
const stateIcons: Record<string, string> = {
  waiting: "pi-clock", viewing: "pi-eye", scanning: "pi-th-large", metadata: "pi-file", renaming: "pi-pencil",
  checking: "pi-sync", activating: "pi-bolt", downloading: "pi-download", idle: "pi-pause", queued: "pi-hourglass",
  ready: "pi-database", error: "pi-exclamation-triangle", slicing: "pi-clone", aligning: "pi-sliders-h", packing: "pi-box",
}
const stateTitle = (state: string) => (state in stateIcons ? t(`series.status.${state}` as MessageKey) : state)

const states = computed(() => props.series.statuses ?? [])
const busy = computed(() => Boolean(props.series.is_busy) || Boolean(props.saving))
// The read of a site page the scan of the series waits for (R-36): pushed by the core, gone when the wait ends.
const reads = useSeriesReadsStore()
const reading = computed(() => {
  const lead = reads.lead(props.series.id)
  return lead ? progressText(lead) : null
})

function layerStyle(layer: string) {
  // idle рисуется зелёным слоем downloading (Р-24); остальное — по имени.
  const mapped = states.value.map((s) => (s === "idle" ? "downloading" : s))
  const active = layerHierarchy.filter((l) => mapped.includes(l))
  const count = active.length
  if (!count) return { width: "0%" }
  const idx = active.indexOf(layer)
  if (idx === -1) return { width: "0%" }
  const width = ((count - idx) / count) * 100
  const style: Record<string, string> = { width: `${width}%` }
  if (width > 0 && width < 99.9) style.boxShadow = "4px 0 12px rgba(0,0,0,0.2)"
  return style
}

const animationClass = computed(() => {
  const s = states.value
  if (s.includes("ready") && s.includes("waiting")) return "stripes-stopped"
  if (s.length === 1) {
    if (["error", "ready"].includes(s[0])) return "stripes-stopped"
    if (["waiting", "viewing"].includes(s[0])) return "stripes-slow"
  }
  const stillActive = ["scanning", "checking", "activating", "renaming", "metadata", "downloading", "slicing", "aligning",
    "packing"]
  if ((s.includes("idle") || s.includes("queued")) && !s.some((x) => stillActive.includes(x)))
    return "stripes-stopped"
  return "stripes-normal"
})

// Priority+: every status that fits is shown; the least important ones (the end of the hierarchy) go into "+N" only when
// the row has no room for them. The widths come from a hidden row of every pill, the room from the row itself.
const pillsRow = ref<HTMLElement | null>(null)
const measureRow = ref<HTMLElement | null>(null)
const fitting = ref(Number.POSITIVE_INFINITY)

function fit(): void {
  const row = pillsRow.value
  const measure = measureRow.value
  if (!row || !measure) return
  const gap = parseFloat(getComputedStyle(row).columnGap) || 0
  const width = (key: string) => (measure.querySelector(`[data-key="${key}"]`) as HTMLElement | null)?.offsetWidth ?? 0
  const others = [...row.children].filter((each) => each.classList.contains("status-pill-other"))
  const room = row.clientWidth - others.reduce((sum, each) => sum + (each as HTMLElement).offsetWidth + gap, 0)
  const widths = states.value.map(width)
  const all = widths.reduce((sum, each) => sum + each + gap, 0) - gap
  if (all <= room) {
    fitting.value = widths.length
    return
  }
  let used = width("overflow")
  let count = 0
  while (count < widths.length && used + gap + widths[count] <= room) used += gap + widths[count++]
  fitting.value = count
}

let observer: ResizeObserver | null = null
onMounted(() => {
  fit()
  if (typeof ResizeObserver !== "undefined" && pillsRow.value) {
    observer = new ResizeObserver(() => fit())
    observer.observe(pillsRow.value)
  }
})
onBeforeUnmount(() => observer?.disconnect())

const pills = computed(() => {
  const s = states.value
  const shown = Math.min(s.length, fitting.value)
  const out: { key: string; title: string; icon: string }[] = []
  if (shown < s.length) out.push({ key: "overflow", title: `+${s.length - shown}`, icon: "pi-ellipsis-h" })
  s.slice(0, shown).forEach((k) => out.push({ key: k, title: stateTitle(k), icon: stateIcons[k] || "" }))
  return out.reverse()
})

// tmdb-прогресс: пилюля скачано/всего, если есть TMDB-инфо с числом эпизодов
const tmdb = computed(() => {
  const info = props.series.tmdb_info as { total_episodes?: number } | null
  const total = info?.total_episodes
  if (total && total > 0) {
    return { downloaded: (props.series.downloaded_episodes_count as number) ?? 0, total }
  }
  return null
})

// The room of the statuses changes with them and with the pills beside them.
watch([states, reading, tmdb], () => nextTick(fit))

const scanTime = computed(() => {
  const iso = props.series.last_scan_time
  if (!iso) return "Никогда"
  return new Date(iso).toLocaleString("ru-RU", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  })
})
</script>

<template>
  <div class="series-card">
    <div class="layer-stripes" :class="animationClass">
      <div
        v-for="l in layerHierarchy"
        :key="l"
        class="color-layer"
        :class="`layer-${l}`"
        :style="layerStyle(l)"
      ></div>
    </div>

    <div class="card-header">
      <div class="series-title-container">
        <span class="series-id unified-text-bg">#{{ series.id }}</span>
        <h3 class="series-title unified-text-bg">{{ series.name }}</h3>
      </div>
      <div class="card-meta">
        <span class="series-site unified-text-bg">{{ series.site }}</span>
        <div class="auto-scan-switch unified-text-bg">
          <span>Готовность</span>
          <label class="switch" :title="series.auto_scan_enabled ? 'Авто-сканирование включено' : 'Авто-сканирование выключено'">
            <input
              type="checkbox"
              :checked="series.auto_scan_enabled"
              @change="emit('toggle-auto-scan', series.id, ($event.target as HTMLInputElement).checked)"
            />
            <span class="slider"></span>
          </label>
        </div>
      </div>
    </div>

    <div class="card-body">
      <div ref="pillsRow" class="status-pills-container">
        <div ref="measureRow" class="status-pills-measure" aria-hidden="true">
          <div v-for="k in states" :key="k" class="status-pill" :data-key="k">
            <i class="pi" :class="stateIcons[k]"></i><span>{{ stateTitle(k) }}</span>
          </div>
          <div class="status-pill" data-key="overflow"><i class="pi pi-ellipsis-h"></i><span>+{{ states.length }}</span></div>
        </div>
        <TransitionGroup name="badge-fade">
          <div v-for="p in pills" :key="p.key" class="status-pill">
            <i v-if="p.icon" class="pi" :class="p.icon"></i>
            <span v-if="p.title">{{ p.title }}</span>
          </div>
        </TransitionGroup>
        <div v-if="reading" class="status-pill status-pill-other" :title="t('series.reading', { text: reading })">
          <i class="pi pi-spin pi-spinner"></i>
          <span>{{ reading }}</span>
        </div>
        <div v-if="tmdb" class="status-pill status-pill-other ms-auto" title="Прогресс эпизодов">
          <i class="pi pi-images"></i>
          <span>{{ tmdb.downloaded }} / {{ tmdb.total }}</span>
        </div>
      </div>
      <div class="scan-time unified-text-bg">{{ scanTime }}</div>
      <div class="card-actions">
        <button class="action-btn btn-status" title="Статус" @click="emit('open-status', series.id)">
          <i class="pi pi-info-circle" />
        </button>
        <button class="action-btn btn-scan" title="Сканировать" :disabled="busy" @click="emit('scan', series.id)">
          <i class="pi pi-refresh" />
        </button>
        <button class="action-btn btn-delete" title="Удалить" :disabled="busy" @click="emit('delete', series.id)">
          <i class="pi pi-trash" />
        </button>
      </div>
    </div>
  </div>
</template>
