<script setup lang="ts">
import { ref, computed, onMounted } from "vue"
import { useToast } from "primevue/usetoast"
import Button from "primevue/button"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"
import { useConfirm } from "../../composables/useConfirm"

// Вкладка «Композиция» (торрент) — порт StatusTabTorrentComposition.
// Файлы сериала, сгруппированные по сезонам, карточками card-torrent;
// кнопка «Переприменить правила» (POST /api/series/{id}/reprocess).
const props = defineProps<{ seriesId: number; seriesName: string }>()
const { request } = useApi()
const toast = useToast()
const confirm = useConfirm()

interface Meta { season?: number; episode?: number; start?: number; end?: number; resolution?: string; quality?: string; voiceover?: string }
interface TFile {
  id: number
  original_path?: string
  renamed_path_preview?: string
  status?: string
  extracted_metadata?: Meta
  is_file_present?: boolean
  is_mismatch?: boolean
  qb_hash?: string
}

const loading = ref(false)
const reprocessing = ref(false)
const files = ref<TFile[]>([])

const groupedFiles = computed(() => {
  const groups: Record<string, TFile[]> = {}
  for (const f of files.value) {
    const season = String(f.extracted_metadata?.season ?? "N/A")
    ;(groups[season] ??= []).push(f)
  }
  for (const k in groups) {
    groups[k].sort((a, b) => {
      const ea = a.extracted_metadata?.episode ?? a.extracted_metadata?.start ?? 0
      const eb = b.extracted_metadata?.episode ?? b.extracted_metadata?.start ?? 0
      return ea - eb
    })
  }
  return groups
})
const sortedSeasons = computed(() =>
  Object.keys(groupedFiles.value).sort((a, b) => {
    if (a === "N/A") return 1
    if (b === "N/A") return -1
    return parseInt(a, 10) - parseInt(b, 10)
  }),
)

function baseName(path?: string): string {
  if (!path) return ""
  return path.split(/[\\/]/).pop() ?? ""
}
function seasonTitle(n: string): string {
  return n === "N/A" ? "Сезон не определён" : `Сезон ${n.padStart(2, "0")}`
}
function sxxexx(m?: Meta): string {
  if (!m) return ""
  const season = String(m.season || 1).padStart(2, "0")
  if (m.episode) return `s${season}e${String(m.episode).padStart(2, "0")}`
  if (m.start && m.end) return `s${season}e${String(m.start).padStart(2, "0")}-e${String(m.end).padStart(2, "0")}`
  if (m.start) return `s${season}e${String(m.start).padStart(2, "0")}`
  return `s${season}`
}
function cardClass(f: TFile): string {
  if (!f.is_file_present) return "status-pending"
  switch (f.status) {
    case "renamed":
      return "status-success"
    case "pending_rename":
      return "status-pending"
    default:
      return "status-no-match"
  }
}

async function load() {
  loading.value = true
  try {
    const data = (await request(
      api.GET("/api/series/{series_id}/composition", { params: { path: { series_id: props.seriesId } } } as never),
      { errorMessage: "Ошибка загрузки композиции" },
    )) as TFile[] | null
    if (Array.isArray(data)) files.value = data
  } finally {
    loading.value = false
  }
}

async function reprocess() {
  const r = await confirm.open({
    title: "Переприменить правила",
    message: "Заново применить правила ко всем файлам этого сериала? Существующие метаданные и имена будут перезаписаны.",
  })
  if (!r.confirmed) return
  reprocessing.value = true
  try {
    const ok = await request(
      api.POST("/api/series/{series_id}/reprocess", { params: { path: { series_id: props.seriesId } } } as never),
      { errorMessage: "Ошибка переобработки" },
    )
    if (ok !== null) {
      toast.add({ severity: "success", summary: "Переобработка", detail: "Задача принята в обработку", life: 3000 })
      setTimeout(load, 2000)
    }
  } finally {
    reprocessing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="status-composition">
    <div class="modern-fieldset">
      <div class="fieldset-header">
        <span class="fieldset-title">Управление файлами</span>
        <Button
          label="Переприменить правила"
          icon="pi pi-refresh"
          size="small"
          :loading="reprocessing"
          :disabled="loading || !files.length"
          @click="reprocess"
        />
      </div>
    </div>

    <div v-if="loading" class="status-loading"><i class="pi pi-spin pi-spinner" style="font-size: 1.6rem" /></div>
    <div v-else-if="!files.length" class="empty-state">
      Нет обработанных файлов. Если сериал только добавлен — запустите сканирование.
    </div>
    <div v-else>
      <div v-for="sn in sortedSeasons" :key="sn" class="season-group">
        <h5 class="season-header"><span>{{ seasonTitle(sn) }}</span></h5>
        <div class="composition-cards-container">
          <div v-for="f in groupedFiles[sn]" :key="f.id" class="card-final card-torrent" :class="cardClass(f)">
            <div class="info-column">
              <div class="card-title-block">
                <span class="card-title" :title="`${seriesName} ${sxxexx(f.extracted_metadata)}`">{{ seriesName }} {{ sxxexx(f.extracted_metadata) }}</span>
                <div v-if="f.extracted_metadata?.resolution" class="quality-badge"><span>{{ f.extracted_metadata.resolution }}</span></div>
              </div>
              <div class="path-line">
                <span class="path-pill">
                  <span class="path-pill-label">Полученное:</span>
                  <span class="path-pill-value" :title="f.original_path">{{ baseName(f.original_path) }}</span>
                </span>
              </div>
              <div class="path-line">
                <span class="path-pill" :class="{ 'is-missing': !f.is_file_present }">
                  <span class="path-pill-label">Фактическое:</span>
                  <span v-if="f.is_file_present" class="path-pill-value" :title="f.renamed_path_preview">{{ baseName(f.renamed_path_preview) }}</span>
                  <span v-else class="path-pill-value"><i class="pi pi-times-circle" /> Файл не найден</span>
                </span>
              </div>
              <div v-if="f.is_mismatch" class="path-line">
                <span class="path-pill is-mismatch">
                  <span class="path-pill-label">Будет:</span>
                  <span class="path-pill-value" :title="f.renamed_path_preview">{{ baseName(f.renamed_path_preview) }}</span>
                </span>
              </div>
            </div>
            <div class="pills-column">
              <div class="pill"><i class="pi pi-check-square" /><span>Статус: <strong>{{ f.status }}</strong></span></div>
              <div class="pill"><i class="pi pi-tags" /><span>Тег: <strong>{{ f.extracted_metadata?.voiceover || "N/A" }}</strong></span></div>
              <div class="pill"><i class="pi pi-video" /><span>Качество: <strong>{{ f.extracted_metadata?.quality || "N/A" }}</strong></span></div>
              <div class="pill"><i class="pi pi-id-card" /><span>ID: <strong>{{ f.id }} / {{ (f.qb_hash ?? "").substring(0, 8) }}</strong></span></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
