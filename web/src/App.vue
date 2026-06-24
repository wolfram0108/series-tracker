<script setup lang="ts">
import Toast from "primevue/toast"
import { useToast } from "primevue/usetoast"
import { ref } from "vue"
import AppHeader from "./components/AppHeader.vue"
import SeriesCard from "./components/SeriesCard.vue"
import ConfirmDialog from "./components/ConfirmDialog.vue"
import SettingsModal from "./components/SettingsModal.vue"
import LogsModal from "./components/LogsModal.vue"
import AddSeriesModal from "./components/AddSeriesModal.vue"
import StatusModal from "./components/StatusModal.vue"
import LoginModal from "./components/LoginModal.vue"
import SetupModal from "./components/SetupModal.vue"
import { useSeriesStore } from "./stores/series"
import { useScannerStore } from "./stores/scanner"
import { useUiStore } from "./stores/ui"
import { useAuthStore } from "./stores/auth"
import { useApi } from "./composables/useApi"
import { useConfirm } from "./composables/useConfirm"
import { useSSE } from "./composables/useSSE"
import { api } from "./api/client"

// Главный экран: шапка + список серий на живых данных из стора. Модалки
// (add / logs / settings / status / confirmation) подключены и рабочие.
const seriesStore = useSeriesStore()
const scannerStore = useScannerStore()
const ui = useUiStore()
const auth = useAuthStore()
const { request } = useApi()
const confirm = useConfirm()
const toast = useToast()

// После успешного входа: переподключить SSE (новый EventSource уже с кукой
// сессии) и перезагрузить данные, упавшие на 401 до входа.
function onLoggedIn() {
  const sse = useSSE()
  sse.disconnect()
  sse.connect()
  void seriesStore.load()
  void scannerStore.load()
}

async function onLogout() {
  await request(api.POST("/api/logout", {} as never))
  auth.reset()
  useSSE().disconnect()
  auth.showLogin = true // снова показать вход
}

// какая модалка открыта (одна за раз). Статус-окно управляется отдельно
// через ui.activeSeriesId (его ставит ui.openStatus + viewing).
const openModal = ref<null | "settings" | "logs" | "add">(null)

async function onScan(id: number) {
  // Ручной скан (по кнопке) уведомляет о результате; ошибку покажет useApi.
  // Автоскан — в фоне и молча (решение пользователя).
  const r = (await request(
    api.POST("/api/series/{series_id}/scan", { params: { path: { series_id: id } } } as never),
  )) as { changed?: boolean } | null
  if (r === null) return
  toast.add(
    r.changed
      ? { severity: "success", summary: "Сканирование завершено", life: 3000 }
      : { severity: "info", summary: "Обновлений нет", life: 3000 },
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
  void ui.openStatus(id) // ставит activeSeriesId + POST state ['viewing']
}
function onCloseStatus() {
  void ui.closeStatus() // POST state [] (снятие viewing)
}
async function onDelete(id: number) {
  const s = seriesStore.byId(id)
  // Чекбокс qBittorrent — только для торрент-серий (у VK торрента нет).
  // delete_from_qb убирает запись из qB; файлы на диске остаются.
  const isTorrent = String(s?.source_type ?? "torrent") === "torrent"
  const r = await confirm.open({
    title: "Удаление сериала",
    message: `Удалить сериал «${s?.name ?? id}»? Это действие необратимо.`,
    ...(isTorrent
      ? { checkbox: { text: "Удалить также записи из qBittorrent (файлы на диске останутся)", checked: true } }
      : {}),
  })
  if (!r.confirmed) return
  const ok = await request(
    api.DELETE("/api/series/{series_id}", {
      params: { path: { series_id: id }, query: { delete_from_qb: r.checkboxChecked } },
    } as never),
  )
  if (ok !== null) seriesStore.remove(id) // оптимистично; SSE series_deleted подтвердит
}
</script>

<template>
  <main class="app">
    <Toast />
    <AppHeader
      @add="openModal = 'add'"
      @logs="openModal = 'logs'"
      @settings="openModal = 'settings'"
      @logout="onLogout"
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

    <SettingsModal v-if="openModal === 'settings'" @close="openModal = null" />
    <LogsModal v-if="openModal === 'logs'" @close="openModal = null" />
    <AddSeriesModal v-if="openModal === 'add'" @close="openModal = null" @created="seriesStore.load()" />
    <StatusModal
      v-if="ui.activeSeriesId"
      :series-id="ui.activeSeriesId"
      @close="onCloseStatus"
      @updated="seriesStore.load()"
    />
    <ConfirmDialog />
    <SetupModal v-if="auth.needsSetup" @done="onLoggedIn" />
    <LoginModal v-else-if="auth.showLogin" @logged-in="onLoggedIn" />
  </main>
</template>
