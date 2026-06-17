import { createApp } from "vue"
import { createPinia } from "pinia"
import PrimeVue from "primevue/config"
import "primeicons/primeicons.css"
import "./styles/tokens.css"
import "./style.css"
import "./styles/fields.css"
import "./styles/overrides.css"
import { STPreset } from "./theme/preset"
import App from "./App.vue"

// Тема — производный пресет под токены series-tracker (Ф2).
// darkModeSelector привязан к классу .st-dark (которого нет) — иначе
// PrimeVue по умолчанию (darkModeSelector: 'system') включает тёмный вид
// при тёмной теме браузера. series-tracker — светлый.
createApp(App)
  .use(createPinia())
  .use(PrimeVue, {
    theme: { preset: STPreset, options: { darkModeSelector: ".st-dark" } },
  })
  .mount("#app")
