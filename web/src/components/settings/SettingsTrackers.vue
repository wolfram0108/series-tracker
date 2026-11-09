<script setup lang="ts">
import { ref, onMounted } from "vue"
import Button from "primevue/button"
import StGroup from "../StGroup.vue"
import StInput from "../StInput.vue"
import StBtn from "../StBtn.vue"
import { api } from "../../api/client"
import type { components } from "../../api/schema"
import { useApi } from "../../composables/useApi"
import { t } from "../../i18n"

// Вкладка «Трекеры»: зеркала доменов по каждому трекеру (mirror-pill +
// добавление/удаление). GET /api/trackers, PUT /api/trackers/{id} {mirrors}.
interface Tracker {
  id: number
  display_name: string
  mirrors: string[]
  newMirror: string
  saving: boolean
}
const trackers = ref<Tracker[]>([])
// Every domain the service reads pages of now, as its registries of trackers and sites give it: for reference only.
const readers = ref<components["schemas"]["ParsedDomains"][]>([])
const { request } = useApi()

async function loadDomains() {
  const data = await request(api.GET("/api/sources/domains"))
  if (data) readers.value = data.readers ?? []
}

async function load() {
  const data = (await request(api.GET("/api/trackers"))) as unknown
  if (!Array.isArray(data)) return
  trackers.value = (data as Array<{ id: number; display_name: string; mirrors?: string[] }>).map((r) => ({
    id: r.id,
    display_name: r.display_name,
    mirrors: r.mirrors ?? [],
    newMirror: "",
    saving: false,
  }))
}

function addMirror(t: Tracker) {
  const v = t.newMirror.trim()
  if (!v) return
  try {
    const host = new URL("https://" + v.replace(/^(https?:\/\/)?/, "")).hostname
    if (!t.mirrors.includes(host)) t.mirrors.push(host)
  } catch {
    // невалидный домен — игнор
  }
  t.newMirror = ""
}
function removeMirror(t: Tracker, i: number) {
  t.mirrors.splice(i, 1)
}
async function saveMirrors(t: Tracker) {
  t.saving = true
  try {
    await request(
      api.PUT("/api/trackers/{tracker_id}", {
        params: { path: { tracker_id: t.id } },
        body: { mirrors: t.mirrors },
      } as never),
      { errorMessage: "Ошибка сохранения зеркал" },
    )
  } finally {
    t.saving = false
  }
}

onMounted(() => {
  void load()
  void loadDomains()
})
</script>

<template>
  <div class="settings-trackers">
    <div v-for="t in trackers" :key="t.id" class="modern-fieldset">
      <div class="fieldset-header">
        <span>{{ t.display_name }}</span>
        <Button
          label="Сохранить"
          icon="pi pi-check"
          size="small"
          :loading="t.saving"
          class="fieldset-save"
          @click="saveMirrors(t)"
        />
      </div>
      <div class="fieldset-content">
        <div v-if="t.mirrors.length" class="mirrors-row">
          <span v-for="(m, i) in t.mirrors" :key="m" class="mirror-pill">
            {{ m }}
            <button class="mirror-pill-remove" title="Удалить" @click="removeMirror(t, i)" />
          </span>
        </div>
        <StGroup>
          <StInput v-model="t.newMirror" label="Новое зеркало (домен)..." />
          <div class="constructor-item item-button-group">
            <StBtn icon="pi pi-plus" variant="add" title="Добавить зеркало" @click="addMirror(t)" />
          </div>
        </StGroup>
      </div>
    </div>
    <div v-if="readers.length" class="modern-fieldset parsed-domains">
      <div class="fieldset-header"><i class="pi pi-globe"></i> {{ t("settings.trackers.domains.title") }}</div>
      <div class="fieldset-content">
        <div v-for="reader in readers" :key="`${reader.kind}:${reader.name}`" class="domains-row">
          <span class="domains-name">{{ reader.name }}</span>
          <span class="domains-kind">{{ t(`settings.trackers.domains.kind.${reader.kind === "tracker" ? "tracker" : "site"}`) }}</span>
          <span v-for="domain in reader.domains ?? []" :key="domain" class="mirror-pill">{{ domain }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.fieldset-save { margin-left: auto; }
.mirrors-row { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.domains-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 6px 0; }
.domains-row + .domains-row { border-top: 1px solid var(--border-color); }
.domains-name { font-weight: 600; }
.domains-kind { color: var(--text-muted); margin-right: 4px; }
</style>
