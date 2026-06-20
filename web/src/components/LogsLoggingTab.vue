<script setup lang="ts">
import { ref, onMounted } from "vue"
import ToggleSwitch from "primevue/toggleswitch"
import { useToast } from "primevue/usetoast"
import { api } from "../api/client"
import { useApi } from "../composables/useApi"

// Вкладка «Настройка» модалки логов (порт SettingsLoggingTab): детализация
// логов по группам модулей (мастер-тумблер на группу включает debug-
// логирование всех её модулей) + флаги сохранения сырых файлов отладки.
// Контракт: GET /api/settings/debug_flags → {logging_modules, file_dump_flags};
// POST {module, enabled}.

interface Mod { name: string; description: string; enabled: boolean }
interface Flag { name: string; description: string; enabled: boolean }

const { request } = useApi()
const toast = useToast()
const loading = ref(true)
const groups = ref<Record<string, Mod[]>>({})
const dumps = ref<Flag[]>([])

async function load() {
  loading.value = true
  const d = (await request(api.GET("/api/settings/debug_flags"), {
    errorMessage: "Ошибка загрузки настроек логирования",
  })) as { logging_modules?: Record<string, Mod[]>; file_dump_flags?: Flag[] } | null
  loading.value = false
  if (d) {
    groups.value = d.logging_modules ?? {}
    dumps.value = d.file_dump_flags ?? []
  }
}

function groupOn(mods: Mod[]): boolean {
  return mods.length > 0 && mods.every((m) => m.enabled)
}

async function postFlag(name: string, enabled: boolean): Promise<boolean> {
  const ok = await request(
    api.POST("/api/settings/debug_flags", { body: { module: name, enabled } } as never),
    { errorMessage: `Ошибка сохранения флага ${name}` },
  )
  return ok !== null
}

async function toggleGroup(g: string, mods: Mod[], value: boolean) {
  mods.forEach((m) => (m.enabled = value)) // оптимистично
  const results = await Promise.all(mods.map((m) => postFlag(m.name, value)))
  if (results.every(Boolean)) {
    toast.add({ severity: "info", summary: g, detail: value ? "логирование включено" : "выключено", life: 2000 })
  } else {
    await load() // откат к серверному состоянию
  }
}

async function toggleFlag(f: Flag) {
  const ok = await postFlag(f.name, f.enabled)
  if (ok) toast.add({ severity: "info", summary: f.description, detail: f.enabled ? "включено" : "выключено", life: 2000 })
  else f.enabled = !f.enabled // откат
}

onMounted(load)
</script>

<template>
  <div class="logging-settings">
    <div v-if="loading" class="logging-loading"><i class="pi pi-spin pi-spinner" style="font-size: 1.6rem" /></div>
    <template v-else>
      <p class="muted-hint">
        Детализация логов по частям приложения. Включение группы активирует debug-логирование для всех её модулей.
      </p>
      <div class="logging-groups">
        <div v-for="(mods, g) in groups" :key="g" class="modern-fieldset">
          <div class="fieldset-header logging-group-header">
            <span class="fieldset-title">{{ g }}</span>
            <ToggleSwitch :model-value="groupOn(mods)" @update:model-value="toggleGroup(g, mods, $event)" />
          </div>
          <div class="fieldset-content">
            <ul class="module-list">
              <li v-for="m in mods" :key="m.name">
                <strong>{{ m.name }}</strong><span class="muted"> — {{ m.description }}</span>
              </li>
            </ul>
          </div>
        </div>
      </div>

      <div class="modern-fieldset">
        <div class="fieldset-header"><i class="pi pi-save"></i> Сохранение файлов отладки</div>
        <div class="fieldset-content">
          <p class="muted-hint">
            При сканировании парсер/скрейпер сохранит полученные сырые данные (HTML/JSON) в папку отладки — для анализа проблем.
          </p>
          <div class="dump-grid">
            <label v-for="f in dumps" :key="f.name" class="debug-switch" :title="f.name">
              <ToggleSwitch v-model="f.enabled" @change="toggleFlag(f)" />
              <span>{{ f.description }}</span>
            </label>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.logging-settings { display: flex; flex-direction: column; gap: 16px; }
.logging-loading { display: flex; justify-content: center; padding: 2rem; color: var(--text-muted); }
.muted-hint { color: var(--text-muted); font-size: 0.88rem; margin: 0 0 8px; }
.logging-groups { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.logging-group-header { display: flex; align-items: center; justify-content: space-between; }
.module-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.module-list li { font-size: 0.85rem; }
.module-list .muted { color: var(--text-muted); }
.dump-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px 20px; }
.debug-switch { display: flex; align-items: center; gap: 10px; cursor: pointer; font-size: 0.9rem; }
@media (max-width: 900px) {
  .logging-groups { grid-template-columns: 1fr; }
  .dump-grid { grid-template-columns: 1fr 1fr; }
}
</style>
