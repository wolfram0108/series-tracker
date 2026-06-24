import { createApp } from "vue"
import { createPinia } from "pinia"
import PrimeVue from "primevue/config"
import ToastService from "primevue/toastservice"
import "primeicons/primeicons.css"
import "./styles/tokens.css"
import "./style.css"
import "./styles/fields.css"
import "./styles/overrides.css"
import "./styles/tables.css"
import "./styles/pills.css"
import "./styles/card.css"
import "./styles/cards.css"
import "./styles/progress.css"
import "./styles/modal.css"
import "./styles/auth-modal.css"
import "./styles/layout.css"
import "./styles/add-series.css"
import "./styles/status.css"
import "./styles/slicing.css"
import "./styles/parser.css"
import { STPreset } from "./theme/preset"
import App from "./App.vue"
import Gallery from "./Gallery.vue"
import { setupRealtime } from "./composables/useRealtime"
import { useSeriesStore } from "./stores/series"
import { useScannerStore } from "./stores/scanner"
import { useAuthStore } from "./stores/auth"
import { api } from "./api/client"

// Галерея согласованных форм/объектов (Ф2) сохранена и доступна по якорю
// #gallery (например /v2#gallery). На обычном адресе монтируется реальное
// приложение. Галерея на мок-данных — real-time/загрузки ей не нужны.
const isGallery = window.location.hash.replace(/^#\/?/, "") === "gallery"

// Тема — производный пресет под токены series-tracker (Ф2).
// darkModeSelector привязан к классу .st-dark (которого нет) — иначе
// PrimeVue по умолчанию (darkModeSelector: 'system') включает тёмный вид
// при тёмной теме браузера. series-tracker — светлый.
createApp(isGallery ? Gallery : App)
  .use(createPinia())
  .use(PrimeVue, {
    theme: { preset: STPreset, options: { darkModeSelector: ".st-dark" } },
  })
  .use(ToastService)
  .mount("#app")

// Ф3: real-time слой — стартовая загрузка + SSE-подписки сторов (только для
// реального приложения; галерее живые данные не нужны).
if (!isGallery) {
  // Перехват 401: запрос, отклонённый замком, открывает модалку входа.
  // /api/login и /api/me исключены — их 401 штатный, не повод для модалки.
  api.use({
    onResponse({ request, response }) {
      if (
        response.status === 401 &&
        !request.url.includes("/api/login") &&
        !request.url.includes("/api/me")
      ) {
        useAuthStore().showLogin = true
      }
      return response
    },
  })

  // Сначала узнаём состояние входа, ПОТОМ грузим данные — иначе 401 от
  // загрузки на миг показал бы модалку входа до того, как выяснится, что
  // нужна установка (первый запуск). Установка имеет приоритет над входом.
  void (async () => {
    try {
      const { data } = await api.GET("/api/auth/status")
      const st = data as
        { configured?: boolean; authenticated?: boolean; username?: string } | undefined
      if (st?.authenticated && st.username) {
        // уже вошли (валидная кука) — восстановить состояние (кнопка выхода)
        useAuthStore().setAuthenticated(st.username)
      } else if (st && !st.configured) {
        useAuthStore().needsSetup = true
      }
    } catch {
      /* статус недоступен — пойдём обычным путём (вход по 401) */
    }
    setupRealtime()
    void useSeriesStore().load()
    void useScannerStore().load()
  })()
}
