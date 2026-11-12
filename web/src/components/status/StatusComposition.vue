<script setup lang="ts">
import { ref, computed, onBeforeUnmount, onMounted } from "vue"
import { useToast } from "primevue/usetoast"
import Button from "primevue/button"
import Checkbox from "primevue/checkbox"
import ToggleSwitch from "primevue/toggleswitch"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"
import { useConfirm } from "../../composables/useConfirm"
import { useSSE } from "../../composables/useSSE"
import { t } from "../../i18n"
import { stateLabel } from "../../lib/states"

// Вкладка «Композиция» (торрент) — порт StatusTabTorrentComposition.
// Файлы сериала, сгруппированные по сезонам, карточками card-torrent;
// кнопка «Переприменить правила» (POST /api/series/{id}/reprocess).
// Состав добывается кнопкой «Получить данные» (composition?refresh=true):
// раздача попадает в клиент и разбирается, закачка не начинается.
const props = defineProps<{ seriesId: number; seriesName: string }>()
const { request } = useApi()
const toast = useToast()
const confirm = useConfirm()

interface Meta { season?: number; episode?: number; start?: number; end?: number; part?: number; resolution?: string; quality?: string; voiceover?: string }
interface TFile {
  id: number | string
  original_path?: string
  renamed_path_preview?: string
  status?: string
  extracted_metadata?: Meta
  is_file_present?: boolean
  is_mismatch?: boolean
  qb_hash?: string
  is_excluded?: boolean
  exclude_reason?: string
}

const loading = ref(false)
const fetching = ref(false)
const reprocessing = ref(false)
const files = ref<TFile[]>([])
// Память выбора: сезоны и номера серий сериала. Живёт в карточке сериала,
// а не в файлах раздачи, поэтому переживает замену релиза.
const ignoredSeasons = ref<number[]>([])
const ignoredEpisodes = ref<Record<string, number[]>>({})

const groupedFiles = computed(() => {
  const groups: Record<string, TFile[]> = {}
  for (const f of files.value) {
    const season = String(f.extracted_metadata?.season ?? "N/A")
    ;(groups[season] ??= []).push(f)
  }
  for (const k in groups) {
    groups[k].sort((a, b) => {
      const ea = a.extracted_metadata?.episode ?? a.extracted_metadata?.start ?? 0
      const eb = b.extracted_metadata?.episode ?? b.extracted_metadata?.start ?? 0
      if (ea !== eb) return ea - eb
      const pa = a.extracted_metadata?.part ?? 0
      const pb = b.extracted_metadata?.part ?? 0
      return pa - pb
    })
  }
  return groups
})
const sortedSeasons = computed(() =>
  Object.keys(groupedFiles.value).sort((a, b) => {
    if (a === "N/A") return 1
    if (b === "N/A") return -1
    return parseInt(a, 10) - parseInt(b, 10)
  }),
)

function baseName(path?: string): string {
  if (!path) return ""
  return path.split(/[\\/]/).pop() ?? ""
}
function seasonTitle(n: string): string {
  return n === "N/A" ? t("composition.season.unknown") : t("output.season", { n: n.padStart(2, "0") })
}
function sxxexx(m?: Meta): string {
  if (!m) return ""
  // Сезон 0 (спешелы) — валидное значение, а не «сезон не задан».
  const season = String(m.season ?? 1).padStart(2, "0")
  const partSuffix = m.part ? ` Part ${m.part}` : ""
  if (m.episode != null) return `s${season}e${String(m.episode).padStart(2, "0")}${partSuffix}`
  if (m.start != null && m.end != null) return `s${season}e${String(m.start).padStart(2, "0")}-e${String(m.end).padStart(2, "0")}${partSuffix}`
  if (m.start != null) return `s${season}e${String(m.start).padStart(2, "0")}${partSuffix}`
  return `s${season}${partSuffix}`
}

// --- выбор серий -----------------------------------------------------------
function fileEpisodes(f: TFile): number[] {
  const m = f.extracted_metadata
  if (!m) return []
  if (m.start != null && m.end != null && m.end >= m.start) {
    return Array.from({ length: m.end - m.start + 1 }, (_, i) => (m.start as number) + i)
  }
  if (m.episode != null) return [m.episode]
  if (m.start != null) return [m.start]
  return []
}
function isUnrecognized(f: TFile): boolean {
  return !fileEpisodes(f).length
}
function isSeasonIgnored(sn: string): boolean {
  return sn !== "N/A" && ignoredSeasons.value.includes(parseInt(sn, 10))
}
function isFileOff(f: TFile): boolean {
  const season = f.extracted_metadata?.season
  if (season != null && ignoredSeasons.value.includes(season)) return true
  const episodes = fileEpisodes(f)
  if (!episodes.length) return true
  const ignored = ignoredEpisodes.value[String(season ?? "")] ?? []
  return episodes.every((e) => ignored.includes(e))
}
function switchable(sn: string): TFile[] {
  return (groupedFiles.value[sn] ?? []).filter((f) => !isUnrecognized(f))
}
function seasonBulkState(sn: string): "on" | "off" | "mixed" {
  const items = switchable(sn)
  if (!items.length) return "on"
  const off = items.filter(isFileOff).length
  if (off === 0) return "on"
  if (off === items.length) return "off"
  return "mixed"
}

async function saveEpisodes(next: Record<string, number[]>): Promise<boolean> {
  const previous = ignoredEpisodes.value
  ignoredEpisodes.value = next // оптимистично: карточка перекрашивается сразу
  const ok = await request(
    api.PUT("/api/series/{series_id}/ignored-episodes", {
      params: { path: { series_id: props.seriesId } },
      body: { episodes: next },
    } as never),
    { errorMessage: t("composition.error.plan") },
  )
  if (ok === null) {
    ignoredEpisodes.value = previous
    return false
  }
  return true
}

function withEpisodes(memory: Record<string, number[]>, season: string, episodes: number[], off: boolean): Record<string, number[]> {
  const next = { ...memory }
  const current = new Set(next[season] ?? [])
  for (const e of episodes) {
    if (off) current.add(e)
    else current.delete(e)
  }
  if (current.size) next[season] = [...current].sort((a, b) => a - b)
  else delete next[season]
  return next
}

async function toggleFile(f: TFile) {
  const season = String(f.extracted_metadata?.season ?? "")
  if (!season) return
  await saveEpisodes(withEpisodes(ignoredEpisodes.value, season, fileEpisodes(f), !isFileOff(f)))
}

async function bulkSetSeason(sn: string) {
  const items = switchable(sn)
  if (!items.length) return
  // Смешанное состояние гасит остаток: следующий клик включит всё.
  const off = seasonBulkState(sn) !== "off"
  let next = ignoredEpisodes.value
  for (const f of items) next = withEpisodes(next, sn, fileEpisodes(f), off)
  if (await saveEpisodes(next)) {
    toast.add({ severity: "success", summary: off ? t("composition.toast.bulkOff") : t("composition.toast.bulkOn"), life: 2000 })
  }
}

async function toggleSeasonIgnored(sn: string) {
  const n = parseInt(sn, 10)
  const was = ignoredSeasons.value.includes(n)
  const previous = ignoredSeasons.value
  ignoredSeasons.value = was ? previous.filter((x) => x !== n) : [...previous, n]
  const ok = await request(
    api.POST("/api/series/{series_id}/ignored-seasons", {
      params: { path: { series_id: props.seriesId } },
      body: { seasons: ignoredSeasons.value },
    } as never),
    { errorMessage: t("composition.error.season") },
  )
  if (ok === null) ignoredSeasons.value = previous
}

function cardClass(f: TFile): string {
  if (isFileOff(f)) return "status-no-match"
  if (!f.is_file_present) return "status-pending"
  switch (f.status) {
    case "renamed":
      return "status-success"
    case "pending_rename":
      return "status-pending"
    default:
      return "status-no-match"
  }
}

function parseList(raw: unknown): number[] {
  try {
    const parsed = typeof raw === "string" ? JSON.parse(raw) : raw
    return Array.isArray(parsed) ? (parsed as number[]) : []
  } catch {
    return []
  }
}
function parseMemory(raw: unknown): Record<string, number[]> {
  try {
    const parsed = typeof raw === "string" ? JSON.parse(raw) : raw
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Record<string, number[]>) : {}
  } catch {
    return {}
  }
}

async function loadSeries() {
  const series = (await request(
    api.GET("/api/series/{series_id}", { params: { path: { series_id: props.seriesId } } } as never),
  )) as Record<string, unknown> | null
  ignoredSeasons.value = parseList(series?.ignored_seasons)
  ignoredEpisodes.value = parseMemory(series?.ignored_episodes)
}

async function load(refresh = false) {
  const busy = refresh ? fetching : loading
  busy.value = true
  try {
    const data = (await request(
      api.GET("/api/series/{series_id}/composition", {
        params: { path: { series_id: props.seriesId }, query: { refresh } },
      } as never),
      { errorMessage: refresh ? t("composition.error.fetch") : t("composition.error.load") },
    )) as TFile[] | null
    if (Array.isArray(data)) files.value = data
  } finally {
    busy.value = false
  }
}

async function fetchData() {
  await load(true)
  await loadSeries()
  toast.add({ severity: "success", summary: t("composition.toast.fetched"), detail: t("composition.toast.fetchedDetail"), life: 4000 })
}

async function reprocess() {
  const r = await confirm.open({
    title: t("composition.reprocess"),
    message: t("composition.reprocess.message"),
  })
  if (!r.confirmed) return
  reprocessing.value = true
  try {
    const ok = await request(
      api.POST("/api/series/{series_id}/reprocess", { params: { path: { series_id: props.seriesId } } } as never),
      { errorMessage: t("composition.error.reprocess") },
    )
    if (ok !== null) {
      toast.add({ severity: "success", summary: t("composition.toast.reprocess"), detail: t("composition.toast.reprocessDetail"), life: 3000 })
    }
  } finally {
    reprocessing.value = false
  }
}

// The end of the renaming is announced by the core (renaming_complete); the composition is read again then.
const stopRenaming = useSSE().on("renaming_complete", (data) => {
  if ((data as { series_id?: number }).series_id === props.seriesId) void load()
})
onBeforeUnmount(stopRenaming)

onMounted(async () => {
  await Promise.all([load(), loadSeries()])
})

// Перечитывание по запросу модалки (переход на вкладку «Композиция»): данные
// могли измениться фоновым усыновлением после «Сохранить» — иначе показывали
// бы состояние на момент открытия модалки до ре-монтажа.
defineExpose({ reload: () => load() })
</script>

<template>
  <div class="status-composition">
    <div class="modern-fieldset">
      <div class="fieldset-header">
        <span class="fieldset-title">{{ t("composition.manage") }}</span>
        <div class="vk-comp-actions">
          <Button
            :label="t('composition.reprocess')"
            icon="pi pi-pencil"
            size="small"
            severity="warn"
            :loading="reprocessing"
            :disabled="loading || fetching || !files.length"
            @click="reprocess"
          />
          <Button
            :label="t('composition.fetch')"
            icon="pi pi-refresh"
            size="small"
            :loading="fetching"
            :disabled="loading"
            @click="fetchData"
          />
        </div>
      </div>
    </div>

    <div v-if="loading || fetching" class="status-loading"><i class="pi pi-spin pi-spinner" style="font-size: 1.6rem" /></div>
    <div v-else-if="!files.length" class="empty-state">
      {{ t("composition.empty") }}
    </div>
    <div v-else>
      <div v-for="sn in sortedSeasons" :key="sn" class="season-group">
        <div class="season-header">
          <span>{{ seasonTitle(sn) }}</span>
          <label v-if="sn !== 'N/A'" class="season-ignore">
            <ToggleSwitch :model-value="!isSeasonIgnored(sn)" @update:model-value="toggleSeasonIgnored(sn)" />
            <span class="season-ignore-label">{{ isSeasonIgnored(sn) ? t("composition.season.ignore") : stateLabel("planned") }}</span>
          </label>
          <label v-if="sn !== 'N/A'" class="season-ignore">
            <Checkbox binary :model-value="seasonBulkState(sn) === 'on'" :indeterminate="seasonBulkState(sn) === 'mixed'" @update:model-value="bulkSetSeason(sn)" />
            <span class="season-ignore-label">{{ t("composition.season.all") }}</span>
          </label>
        </div>
        <div class="composition-cards-container">
          <div v-for="f in groupedFiles[sn]" :key="f.id" class="card-final card-torrent" :class="cardClass(f)">
            <div class="info-column">
              <div class="card-title-block">
                <span class="card-title" :title="`${seriesName} ${sxxexx(f.extracted_metadata)}`">{{ seriesName }} {{ sxxexx(f.extracted_metadata) }}</span>
                <div v-if="f.extracted_metadata?.resolution" class="quality-badge"><span>{{ f.extracted_metadata.resolution }}</span></div>
              </div>
              <div class="path-line">
                <span class="path-pill">
                  <span class="path-pill-label">{{ t("composition.label.received") }}</span>
                  <span class="path-pill-value" :title="f.original_path">{{ baseName(f.original_path) }}</span>
                </span>
              </div>
              <div class="path-line">
                <span class="path-pill" :class="{ 'is-missing': !f.is_file_present && !isFileOff(f) }">
                  <span class="path-pill-label">{{ t("composition.label.actual") }}</span>
                  <span v-if="isUnrecognized(f)" class="path-pill-value">{{ t("composition.file.unrecognized") }}</span>
                  <span v-else-if="isFileOff(f)" class="path-pill-value">{{ t("composition.file.off") }}</span>
                  <span v-else-if="f.is_file_present" class="path-pill-value" :title="f.renamed_path_preview">{{ baseName(f.renamed_path_preview) }}</span>
                  <span v-else class="path-pill-value"><i class="pi pi-times-circle" /> {{ t("composition.file.missing") }}</span>
                </span>
              </div>
              <div v-if="f.is_mismatch" class="path-line">
                <span class="path-pill is-mismatch">
                  <span class="path-pill-label">{{ t("composition.label.will") }}</span>
                  <span class="path-pill-value" :title="f.renamed_path_preview">{{ baseName(f.renamed_path_preview) }}</span>
                </span>
              </div>
            </div>
            <div class="pills-column">
              <div v-if="f.extracted_metadata?.part" class="pill"><i class="pi pi-clone" /><span>{{ t("composition.label.part") }} <strong>{{ f.extracted_metadata.part }}</strong></span></div>
              <div class="pill"><i class="pi pi-check-square" /><span>{{ t("composition.label.status") }} <strong>{{ f.status }}</strong></span></div>
              <div class="pill"><i class="pi pi-tags" /><span>{{ t("composition.label.tag") }} <strong>{{ f.extracted_metadata?.voiceover || "N/A" }}</strong></span></div>
              <div class="pill"><i class="pi pi-video" /><span>{{ t("composition.label.quality") }} <strong>{{ f.extracted_metadata?.quality || "N/A" }}</strong></span></div>
              <div class="pill"><i class="pi pi-id-card" /><span>{{ t("composition.label.id") }} <strong>{{ f.id }} / {{ (f.qb_hash ?? "").substring(0, 8) }}</strong></span></div>
            </div>
            <div v-if="!isUnrecognized(f)" class="controls-column">
              <ToggleSwitch :model-value="!isFileOff(f)" @update:model-value="toggleFile(f)" />
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
