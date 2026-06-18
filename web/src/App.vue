<script setup lang="ts">
import Toast from "primevue/toast"
import { useToast } from "primevue/usetoast"
import AppHeader from "./components/AppHeader.vue"
import SeriesCard from "./components/SeriesCard.vue"
import ConfirmDialog from "./components/ConfirmDialog.vue"
import { useSeriesStore } from "./stores/series"
import { useUiStore } from "./stores/ui"
import { useApi } from "./composables/useApi"
import { useConfirm } from "./composables/useConfirm"
import { api } from "./api/client"

// Главный экран (каркас Ф4): шапка + список серий на живых данных из стора.
// Модалки (add/logs/settings/status/confirmation) — следующие под-вехи Ф4,
// пока кнопки дают информативную заглушку.
const seriesStore = useSeriesStore()
const ui = useUiStore()
const { request } = useApi()
const confirm = useConfirm()
const toast = useToast()

function onScan(id: number) {
  void request(
    api.POST("/api/series/{series_id}/scan", { params: { path: { series_id: id } } } as never),
  )
}
function onToggle(id: number, enabled: boolean) {
  void request(
    api.POST("/api/series/{series_id}/toggle_auto_scan", {
      params: { path: { series_id: id } },
      body: { enabled },
    } as never),
  )
}
function onOpenStatus(id: number) {
  void ui.openStatus(id)
  toast.add({ severity: "info", summary: "Статус", detail: "Окно статуса — в следующей под-вехе Ф4", life: 3000 })
}
async function onDelete(id: number) {
  const s = seriesStore.byId(id)
  const r = await confirm.open({
    title: "Удаление сериала",
    message: `Удалить сериал «${s?.name ?? id}»? Это действие необратимо.`,
    checkbox: { text: "Также удалить торрент и файлы из qBittorrent", checked: false },
  })
  if (!r.confirmed) return
  const ok = await request(
    api.DELETE("/api/series/{series_id}", {
      params: { path: { series_id: id }, query: { delete_from_qb: r.checkboxChecked } },
    } as never),
  )
  if (ok !== null) seriesStore.remove(id) // оптимистично; SSE series_deleted подтвердит
}
function stub(name: string) {
  toast.add({ severity: "info", summary: name, detail: "Модалка — в следующей под-вехе Ф4", life: 3000 })
}
</script>

<template>
  <main class="app">
    <Toast />
    <AppHeader
      @add="stub('Добавить сериал')"
      @logs="stub('Просмотр логов')"
      @settings="stub('Настройки')"
    />
    <div class="series-list">
      <SeriesCard
        v-for="s in seriesStore.list"
        :key="s.id"
        :series="s"
        :saving="seriesStore.isSaving(s.id)"
        @open-status="onOpenStatus"
        @scan="onScan"
        @delete="onDelete"
        @toggle-auto-scan="onToggle"
      />
      <div v-if="!seriesStore.list.length" class="empty-state">Нет сериалов. Добавьте первый.</div>
    </div>

    <ConfirmDialog />
  </main>
</template>
