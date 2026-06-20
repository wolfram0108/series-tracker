<script setup lang="ts">
import { ref, onMounted } from "vue"
import Button from "primevue/button"
import StGroup from "../StGroup.vue"
import StInput from "../StInput.vue"
import StBtn from "../StBtn.vue"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"

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
const { request } = useApi()

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

onMounted(load)
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
            <button class="mirror-pill-remove" title="Удалить" @click="removeMirror(t, i)"><i class="pi pi-times" /></button>
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
  </div>
</template>

<style scoped>
.fieldset-save { margin-left: auto; }
.mirrors-row { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
</style>
