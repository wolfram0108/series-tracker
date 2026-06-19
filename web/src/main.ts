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
import "./styles/layout.css"
import "./styles/add-series.css"
import "./styles/status.css"
import "./styles/slicing.css"
import { STPreset } from "./theme/preset"
import App from "./App.vue"
import Gallery from "./Gallery.vue"
import { setupRealtime } from "./composables/useRealtime"
import { useSeriesStore } from "./stores/series"
import { useScannerStore } from "./stores/scanner"

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
  setupRealtime()
  void useSeriesStore().load()
  void useScannerStore().load()
}
