<script setup lang="ts">
import { ref, onMounted } from "vue"
import Button from "primevue/button"
import ModalShell from "./ModalShell.vue"
import StGroup from "./StGroup.vue"
import StIcon from "./StIcon.vue"
import StInput from "./StInput.vue"
import StSelect from "./StSelect.vue"
import StBtn from "./StBtn.vue"
import { api } from "../api/client"
import { useApi } from "../composables/useApi"

// Модалка «Добавить сериал» — КАРКАС (этап 1): ввод URL → распознавание
// источника (POST /api/parse_url) → базовая информация → сохранение
// (POST /api/series). TMDB-распознаватель, saved-path-dropdown, VK-настройки
// и выбор качества — следующие итерации этой вехи.
const emit = defineEmits<{ (e: "close"): void; (e: "created"): void }>()
const { request } = useApi()

const url = ref("")
const parsing = ref(false)
const parsed = ref(false)
const urlError = ref("")
const saving = ref(false)

interface ParserData { source_type?: string; torrents?: unknown[] }
const parserData = ref<ParserData | null>(null)

const ns = ref({
  url: "",
  name: "",
  name_en: "",
  save_path: "",
  season: "s01",
  parser_profile_id: null as number | null,
  vk_search_mode: "search",
})

const profileOptions = ref<{ label: string; value: number | null }[]>([{ label: "По умолчанию", value: null }])

async function loadProfiles() {
  const data = (await request(api.GET("/api/parser-profiles"))) as unknown
  if (Array.isArray(data)) {
    profileOptions.value = [
      { label: "По умолчанию", value: null },
      ...(data as Array<{ id: number; name: string }>).map((p) => ({ label: p.name, value: p.id })),
    ]
  }
}

async function parseUrl() {
  urlError.value = ""
  parsed.value = false
  if (!url.value.trim()) return
  parsing.value = true
  const data = (await request(
    api.POST("/api/parse_url", { body: { url: url.value.trim() } } as never),
    { errorMessage: "Ошибка распознавания URL" },
  )) as { title?: { ru?: string; en?: string }; source_type?: string; torrents?: unknown[] } | null
  parsing.value = false
  if (!data) {
    urlError.value = "Не удалось распознать ссылку"
    return
  }
  ns.value.url = url.value.trim()
  ns.value.name = data.title?.ru ?? ""
  ns.value.name_en = data.title?.en ?? ""
  parserData.value = { source_type: data.source_type, torrents: data.torrents }
  parsed.value = true
}

async function save() {
  saving.value = true
  try {
    const payload = {
      ...ns.value,
      source_type: parserData.value?.source_type ?? "torrent",
      quality: "",
      torrents: parserData.value?.torrents ?? [],
    }
    const ok = await request(api.POST("/api/series", { body: payload } as never), {
      errorMessage: "Ошибка добавления сериала",
    })
    if (ok !== null) {
      emit("created")
      emit("close")
    }
  } finally {
    saving.value = false
  }
}

onMounted(loadProfiles)
</script>

<template>
  <ModalShell title="Добавить сериал" @close="emit('close')">
    <StGroup>
      <StIcon icon="pi pi-link" />
      <StInput v-model="url" label="Ссылка на раздачу / канал" />
      <div class="constructor-item item-button-group">
        <StBtn icon="pi pi-search" variant="search" title="Распознать" @click="parseUrl" />
      </div>
    </StGroup>
    <p v-if="parsing" class="add-hint">Распознаём ссылку…</p>
    <p v-if="urlError" class="add-error">{{ urlError }}</p>

    <div v-if="parsed" class="add-info">
      <div class="modern-fieldset">
        <div class="fieldset-header"><i class="pi pi-info-circle"></i> Информация о сериале</div>
        <div class="fieldset-content">
          <StGroup>
            <StIcon icon="pi pi-bookmark" />
            <StInput v-model="ns.name" label="Название (рус)" />
            <StIcon icon="pi pi-bookmark" />
            <StInput v-model="ns.name_en" label="Название (англ)" />
          </StGroup>
          <StGroup>
            <StIcon icon="pi pi-folder" />
            <StInput v-model="ns.save_path" label="Путь сохранения" />
          </StGroup>
          <StGroup>
            <StIcon icon="pi pi-hashtag" />
            <StInput v-model="ns.season" label="Сезон (напр. s01)" />
            <StIcon icon="pi pi-filter" />
            <StSelect v-model="ns.parser_profile_id" :options="profileOptions" placeholder="Профиль парсера" />
          </StGroup>
        </div>
      </div>
    </div>

    <template #footer>
      <Button v-if="parsed" label="Добавить" icon="pi pi-check" :loading="saving" @click="save" />
      <Button label="Отмена" icon="pi pi-times" severity="secondary" @click="emit('close')" />
    </template>
  </ModalShell>
</template>

<style scoped>
.add-hint { color: var(--text-muted); margin: 8px 0 0; }
.add-error { color: var(--color-red-1); margin: 8px 0 0; }
.add-info { margin-top: 16px; }
</style>
