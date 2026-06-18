<script setup lang="ts">
import { computed } from "vue"
import type { Series } from "../stores/series"

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
  "waiting", "ready", "downloading", "queued", "activating",
  "checking", "renaming", "metadata", "scanning", "viewing", "error",
]
const stateConfig: Record<string, { title: string; icon: string }> = {
  waiting: { title: "Ожидание", icon: "pi-clock" },
  viewing: { title: "Просмотр", icon: "pi-eye" },
  scanning: { title: "Сканирование", icon: "pi-th-large" },
  metadata: { title: "Метадата", icon: "pi-file" },
  renaming: { title: "Переименование", icon: "pi-pencil" },
  checking: { title: "Проверка", icon: "pi-sync" },
  activating: { title: "Активация", icon: "pi-bolt" },
  downloading: { title: "Загрузка", icon: "pi-download" },
  idle: { title: "Простой", icon: "pi-pause" },
  queued: { title: "В очереди", icon: "pi-hourglass" },
  ready: { title: "Готов", icon: "pi-database" },
  error: { title: "Ошибка", icon: "pi-exclamation-triangle" },
}

const states = computed(() => props.series.statuses ?? [])
const busy = computed(() => Boolean(props.series.is_busy) || Boolean(props.saving))

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
  const stillActive = ["scanning", "checking", "activating", "renaming", "metadata", "downloading"]
  if ((s.includes("idle") || s.includes("queued")) && !s.some((x) => stillActive.includes(x)))
    return "stripes-stopped"
  return "stripes-normal"
})

const pills = computed(() => {
  const s = states.value
  const maxVisible = 3
  const out: { key: string; title: string; icon: string }[] = []
  let visible = s
  if (s.length > maxVisible) {
    visible = s.slice(0, maxVisible)
    out.push({ key: "overflow", title: `+${s.length - maxVisible}`, icon: "pi-ellipsis-h" })
  }
  visible.forEach((k) =>
    out.push({ key: k, title: stateConfig[k]?.title || k, icon: stateConfig[k]?.icon || "" }))
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
      <div style="display: flex; align-items: center; gap: 8px">
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
      <div class="status-pills-container">
        <TransitionGroup name="badge-fade">
          <div v-for="p in pills" :key="p.key" class="status-pill">
            <i v-if="p.icon" class="pi" :class="p.icon"></i>
            <span v-if="p.title">{{ p.title }}</span>
          </div>
        </TransitionGroup>
        <div v-if="tmdb" class="status-pill ms-auto" title="Прогресс эпизодов">
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
