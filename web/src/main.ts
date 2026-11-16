import { createApp, type Component } from "vue"
import { createPinia } from "pinia"
import PrimeVue from "primevue/config"
import ToastService from "primevue/toastservice"
import "primeicons/primeicons.css"
import "./styles/tokens.css"
import "./styles/states.css"
import "./styles/primitives.css"
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
import "./styles/output-block.css"
import "./styles/queues.css"
import "./styles/work.css"
import "./styles/slicing.css"
import "./styles/parser.css"
import { STPreset } from "./theme/preset"
import App from "./App.vue"
import { setupRealtime } from "./composables/useRealtime"
import { useSeriesStore } from "./stores/series"
import { useScannerStore } from "./stores/scanner"
import { useAuthStore } from "./stores/auth"
import { api } from "./api/client"

// Pages on samples instead of the application, by the anchor: #gallery, the gallery of the agreed forms (Ф-2), #card-colors,
// the colours of the card of an incomplete episode on the real components. They need no live data, and the application
// does not need them: each is a chunk of the build loaded only by its anchor.
const PAGES = {
  gallery: () => import("./Gallery.vue"),
  "card-colors": () => import("./tools/CardColors.vue"),
} as const
const sample = PAGES[window.location.hash.replace(/^#\/?/, "") as keyof typeof PAGES]
const onSamples = sample !== undefined

// Тема — производный пресет под токены series-tracker (Ф2).
// darkModeSelector привязан к классу .st-dark (которого нет) — иначе
// PrimeVue по умолчанию (darkModeSelector: 'system') включает тёмный вид
// при тёмной теме браузера. series-tracker — светлый.
function mount(page: Component): void {
  createApp(page)
    .use(createPinia())
    .use(PrimeVue, {
      theme: { preset: STPreset, options: { darkModeSelector: ".st-dark" } },
    })
    .use(ToastService)
    .mount("#app")
}
if (sample) void sample().then((module) => mount(module.default))
else mount(App)

// Ф-3: the real-time layer, the first load and the SSE subscriptions of the stores (the application only: the pages on
// samples need no live data).
if (!onSamples) {
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
      const st = data as {
        configured?: boolean; authenticated?: boolean
        username?: string; admin?: string
      } | undefined
      const auth = useAuthStore()
      // имя администратора для экрана входа (показываем вместо поля логина)
      if (st?.admin) auth.adminName = st.admin
      if (st?.authenticated && st.username) {
        // уже вошли (валидная кука) — восстановить состояние (кнопка выхода)
        auth.setAuthenticated(st.username)
      } else if (st && !st.configured) {
        auth.needsSetup = true
      }
    } catch {
      /* статус недоступен — пойдём обычным путём (вход по 401) */
    }
    setupRealtime()
    void useSeriesStore().load()
    void useScannerStore().load()
  })()
}
