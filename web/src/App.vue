<script setup lang="ts">
import Button from "primevue/button"
import Toast from "primevue/toast"
import { useToast } from "primevue/usetoast"
import { ref } from "vue"
import { t } from "./i18n"
import AppHeader from "./components/AppHeader.vue"
import ConnectionBanner from "./components/ConnectionBanner.vue"
import SeriesList from "./components/SeriesList.vue"
import ConfirmDialog from "./components/ConfirmDialog.vue"
import SettingsModal from "./components/SettingsModal.vue"
import ExtensionsModal from "./components/ExtensionsModal.vue"
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
import { useAction } from "./composables/useAction"
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
  await request(api.POST("/api/logout", {}))
  auth.reset()
  useSSE().disconnect()
  auth.showLogin = true // снова показать вход
}

// какая модалка открыта (одна за раз). Статус-окно управляется отдельно
// через ui.activeSeriesId (его ставит ui.openStatus + viewing).
const openModal = ref<null | "settings" | "extensions" | "logs" | "add">(null)

// A press that came while the same action of the same series is not answered is not an action of its own (composables/useAction.ts).
const cancelScan = useAction(async (id: number) => {
  const r = (await request(
    api.POST("/api/series/{series_id}/scan/cancel", { params: { path: { series_id: id } } }),
  )) as { cancelled?: boolean } | null
  if (r === null) return
  toast.add({ severity: "info", summary: t(r.cancelled ? "sync.stopped" : "sync.notRunning"), life: 3000 })
}, (id) => String(id))

const scan = useAction(async (id: number) => {
  // Ручной скан (по кнопке) уведомляет о результате; ошибку покажет useApi.
  // Автоскан — в фоне и молча (решение пользователя).
  const r = (await request(
    api.POST("/api/series/{series_id}/scan", { params: { path: { series_id: id } } }),
  )) as { changed?: boolean; cancelled?: boolean } | null
  if (r === null || r.cancelled) return  // a stopped sync is told by its stop
  toast.add(
    r.changed
      ? { severity: "success", summary: t("app.sync.done"), life: 3000 }
      : { severity: "info", summary: t("app.sync.noUpdates"), life: 3000 },
  )
}, (id) => String(id))
const toggleAuto = useAction(async (id: number, enabled: boolean) => {
  await request(
    api.POST("/api/series/{series_id}/toggle_auto_scan", {
      params: { path: { series_id: id } },
      body: { enabled },
    }),
  )
}, (id) => String(id))
function onOpenStatus(id: number) {
  void ui.openStatus(id) // ставит activeSeriesId + POST state ['viewing']
}
function onCloseStatus() {
  void ui.closeStatus() // POST state [] (снятие viewing)
}
const remove = useAction(async (id: number) => {
  const s = seriesStore.byId(id)
  const r = await confirm.open({
    title: t("app.delete.title"),
    message: t("app.delete.message", { name: s?.name ?? id }),
  })
  if (!r.confirmed) return
  const ok = await request(
    api.DELETE("/api/series/{series_id}", {
      params: { path: { series_id: id } },
    }),
  )
  if (ok !== null) seriesStore.remove(id) // оптимистично; SSE series_deleted подтвердит
}, (id) => String(id))
</script>

<template>
  <main class="app">
    <Toast />
    <ConnectionBanner />
    <AppHeader
      @add="openModal = 'add'"
      @logs="openModal = 'logs'"
      @extensions="openModal = 'extensions'"
      @settings="openModal = 'settings'"
      @logout="onLogout"
    />
    <SeriesList
      @open-status="onOpenStatus"
      @scan="scan.run"
      @cancel-scan="cancelScan.run"
      @delete="remove.run"
      @toggle-auto-scan="toggleAuto.run"
    >
      <!-- Nothing is said of "no series" before the list has been read, and nothing of it where it could not be read. -->
      <div v-if="!seriesStore.list.length" class="empty-state" :role="seriesStore.loaded ? undefined : 'status'">
        <template v-if="seriesStore.failed">
          {{ t("app.loadFailed") }}
          <div><Button class="empty-retry" :label="t('common.retry')" icon="pi pi-refresh" severity="secondary" @click="seriesStore.load()" /></div>
        </template>
        <template v-else-if="!seriesStore.loaded"><i class="pi pi-spin pi-spinner" aria-hidden="true" /> {{ t("app.loading") }}</template>
        <template v-else>{{ t("app.empty") }}</template>
      </div>
    </SeriesList>

    <SettingsModal v-if="openModal === 'settings'" @close="openModal = null" />
    <ExtensionsModal v-if="openModal === 'extensions'" @close="openModal = null" />
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
