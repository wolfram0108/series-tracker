import { createApp } from "vue"
import { createPinia } from "pinia"
import PrimeVue from "primevue/config"
import "primeicons/primeicons.css"
import "./styles/tokens.css"
import "./style.css"
import { STPreset } from "./theme/preset"
import App from "./App.vue"

// Тема — производный пресет под токены series-tracker (Ф2).
createApp(App)
  .use(createPinia())
  .use(PrimeVue, { theme: { preset: STPreset } })
  .mount("#app")
