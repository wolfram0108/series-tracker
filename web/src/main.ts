import { createApp } from "vue"
import { createPinia } from "pinia"
import PrimeVue from "primevue/config"
import Aura from "@primevue/themes/aura"
import "primeicons/primeicons.css"
import "./style.css"
import App from "./App.vue"

// Тема — пресет Aura (в Ф2 заменим на производный под токены series-tracker).
createApp(App)
  .use(createPinia())
  .use(PrimeVue, { theme: { preset: Aura } })
  .mount("#app")
