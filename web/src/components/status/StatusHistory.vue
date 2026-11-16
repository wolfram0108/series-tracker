<script setup lang="ts">
import { ref, onMounted } from "vue"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"
import { t } from "../../i18n"
import { useBusyBar } from "../../composables/useWindowBusy"
import LoadingMark from "../LoadingMark.vue"

// The tab «История» of a torrent series: its releases, the active one and those it replaced (GET torrents/history).
// Only a view.
const props = defineProps<{ seriesId: number }>()
const { request } = useApi()

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
const loading = ref(false)
useBusyBar(() => loading.value)
const torrentHistory = ref<TorrentRow[]>([])

async function load() {
  loading.value = true
  try {
    const data = (await request(
      api.GET("/api/series/{series_id}/torrents/history", { params: { path: { series_id: props.seriesId } } }),
      { errorMessage: t("history.error.torrents") },
    )) as TorrentRow[] | null
    if (Array.isArray(data)) torrentHistory.value = data
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="status-history">
    <div v-if="loading" class="status-loading"><LoadingMark /></div>

    <template v-else>
      <h3 class="status-history-title">{{ t("history.torrents.title") }}</h3>
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
          <div v-if="!torrentHistory.length" class="div-table-row"><div class="div-table-cell span-all">{{ t("common.none") }}</div></div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.status-history-title { margin: 0 0 0.75rem; font-size: 1rem; font-weight: 600; }
</style>
