<script setup lang="ts">
import { ref, onMounted } from "vue"
import Button from "primevue/button"
import Tag from "primevue/tag"

// Проба сквозной связи с бэком (через dev-proxy или раздачу под /v2):
// дёргаем типизированный позже /api/scanner/status.
const backend = ref<string>("проверяем…")
const severity = ref<"success" | "danger" | "warn">("warn")

onMounted(async () => {
  try {
    const r = await fetch("/api/scanner/status")
    if (r.ok) {
      backend.value = "бэкенд доступен"
      severity.value = "success"
    } else {
      backend.value = `бэкенд: ошибка ${r.status}`
      severity.value = "danger"
    }
  } catch {
    backend.value = "бэкенд недоступен"
    severity.value = "danger"
  }
})
</script>

<template>
  <main class="shell">
    <h1>Series Tracker <small>/v2</small></h1>
    <p class="muted">
      Каркас нового фронта: Vite + Vue 3 SFC + TypeScript + PrimeVue + Pinia.
      Фаза Ф1 (frontend-rewrite). Старый фронт работает на «/».
    </p>
    <Tag :value="backend" :severity="severity" />
    <div class="row">
      <Button label="PrimeVue Button" icon="pi pi-check" />
      <Button label="Outlined" icon="pi pi-cog" severity="secondary" outlined />
    </div>
  </main>
</template>

<style scoped>
.shell { max-width: 720px; margin: 48px auto; padding: 0 16px; }
h1 { margin-bottom: 8px; }
small { color: #888; font-weight: 400; font-size: 0.6em; }
.muted { color: #6c757d; }
.row { display: flex; gap: 12px; margin-top: 16px; }
</style>
