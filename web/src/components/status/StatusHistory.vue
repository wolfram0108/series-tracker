<script setup lang="ts">
import { ref, onMounted } from "vue"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"
import { t } from "../../i18n"

// Вкладка «История» — порт StatusTabHistory. Торрент: таблица истории
// торрентов (GET torrents/history). VK: таблица медиа-элементов в БД
// (GET media-items). Только просмотр.
const props = defineProps<{ seriesId: number; kind: string }>()
const { request } = useApi()
const isVideo = props.kind === "video"
const isVk = isVideo

interface TorrentRow {
  id: number
  torrent_id?: string
  link?: string
  date_time?: string
  episodes?: string
  quality?: string
  is_active?: boolean
  qb_hash?: string | null
}
interface MediaRow {
  id: number
  unique_id?: string
  source_url?: string
  season?: number
  episode_start?: number
  episode_end?: number
  status?: string
  final_filename?: string
  chapters?: string | null
  publication_date?: string
}

const loading = ref(false)
const torrentHistory = ref<TorrentRow[]>([])
const mediaHistory = ref<MediaRow[]>([])

function episodeInfo(m: MediaRow): string {
  const season = `s${String(m.season ?? 1).padStart(2, "0")}`
  const ep = m.episode_end
    ? `e${String(m.episode_start).padStart(2, "0")}-e${String(m.episode_end).padStart(2, "0")}`
    : `e${String(m.episode_start).padStart(2, "0")}`
  return `${season}${ep}`
}
function chapterStatus(m: MediaRow): string {
  if (!m.chapters) return t("common.no")
  try {
    const ch = JSON.parse(m.chapters) as unknown[]
    return ch.length > 0 ? t("history.chapters.yes", { count: ch.length }) : t("common.error")
  } catch {
    return t("common.error")
  }
}
function fmtDate(iso?: string): string {
  if (!iso) return "—"
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" })
}

async function load() {
  loading.value = true
  try {
    if (isVk) {
      const data = (await request(
        api.GET("/api/series/{series_id}/media-items", { params: { path: { series_id: props.seriesId } } } as never),
        { errorMessage: t("history.error.media") },
      )) as MediaRow[] | null
      if (Array.isArray(data)) mediaHistory.value = data
    } else {
      const data = (await request(
        api.GET("/api/series/{series_id}/torrents/history", { params: { path: { series_id: props.seriesId } } } as never),
        { errorMessage: t("history.error.torrents") },
      )) as TorrentRow[] | null
      if (Array.isArray(data)) torrentHistory.value = data
    }
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="status-history">
    <div v-if="loading" class="status-loading"><i class="pi pi-spin pi-spinner" style="font-size: 1.6rem" /></div>

    <template v-else-if="isVk">
      <h6 class="status-history-title">{{ t("history.vk.title") }}</h6>
      <div class="status-history-scroll">
        <div class="div-table table-media-item-history">
          <div class="div-table-header">
            <div class="div-table-cell">Unique ID</div>
            <div class="div-table-cell">URL</div>
            <div class="div-table-cell">{{ t("common.episodes") }}</div>
            <div class="div-table-cell">{{ t("common.status") }}</div>
            <div class="div-table-cell">{{ t("common.file") }}</div>
            <div class="div-table-cell">{{ t("history.col.chapters") }}</div>
            <div class="div-table-cell">{{ t("common.date") }}</div>
          </div>
          <div class="div-table-body">
            <div v-for="m in mediaHistory" :key="m.id" class="div-table-row">
              <div class="div-table-cell">{{ m.unique_id }}</div>
              <div class="div-table-cell"><a :href="m.source_url" target="_blank">{{ m.source_url }}</a></div>
              <div class="div-table-cell">{{ episodeInfo(m) }}</div>
              <div class="div-table-cell">{{ m.status }}</div>
              <div class="div-table-cell">{{ m.final_filename }}</div>
              <div class="div-table-cell">{{ chapterStatus(m) }}</div>
              <div class="div-table-cell">{{ fmtDate(m.publication_date) }}</div>
            </div>
            <div v-if="!mediaHistory.length" class="div-table-row"><div class="div-table-cell" style="grid-column: 1 / -1">{{ t("common.none") }}</div></div>
          </div>
        </div>
      </div>
    </template>

    <template v-else>
      <h6 class="status-history-title">{{ t("history.torrents.title") }}</h6>
      <div class="div-table table-torrents-history">
        <div class="div-table-header">
          <div class="div-table-cell">ID</div>
          <div class="div-table-cell">{{ t("common.link") }}</div>
          <div class="div-table-cell">{{ t("common.date") }}</div>
          <div class="div-table-cell">{{ t("common.episodes") }}</div>
          <div class="div-table-cell">{{ t("common.quality") }}</div>
          <div class="div-table-cell">{{ t("history.col.active") }}</div>
          <div class="div-table-cell">{{ t("history.col.hash") }}</div>
        </div>
        <div class="div-table-body">
          <div v-for="row in torrentHistory" :key="row.id" class="div-table-row">
            <div class="div-table-cell">{{ row.torrent_id }}</div>
            <div class="div-table-cell">{{ row.link }}</div>
            <div class="div-table-cell">{{ row.date_time }}</div>
            <div class="div-table-cell">{{ row.episodes ?? "—" }}</div>
            <div class="div-table-cell">{{ row.quality ?? "—" }}</div>
            <div class="div-table-cell">{{ row.is_active ? t("common.yes") : t("common.no") }}</div>
            <div class="div-table-cell">{{ row.qb_hash || "N/A" }}</div>
          </div>
          <div v-if="!torrentHistory.length" class="div-table-row"><div class="div-table-cell" style="grid-column: 1 / -1">{{ t("common.none") }}</div></div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.status-history-title { margin: 0 0 12px; font-size: 1rem; font-weight: 600; }
.status-history-scroll { overflow-x: auto; }
</style>
