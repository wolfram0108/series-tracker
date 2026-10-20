<script setup lang="ts">
import { ref, computed, watch } from "vue"
import { useToast } from "primevue/usetoast"
import Button from "primevue/button"
import StGroup from "../StGroup.vue"
import StIcon from "../StIcon.vue"
import StInput from "../StInput.vue"
import { useApi } from "../../composables/useApi"
import { api } from "../../api/client"
import { useVideoScanStore } from "../../stores/videoScan"

// Шаг 3: тестирование профиля. Сбор «сырых» названий с VK (или имён
// файлов сериала по ID#N) + прогон правил профиля и карточки результата.
// Порт static/js/components/settingsParser.js (scrapeTestTitles, runTest,
// getResultClass, testResultPills, formatResolution).

// initialTitles: если передан (окно добавления сериала) — предзаполняет поле
// теста списком файлов раздачи. В Настройках не передаётся → поведение как было.
const props = defineProps<{ profileId: number; initialTitles?: string[] }>()

const { request } = useApi()
const toast = useToast()
const videoScan = useVideoScanStore()

const scrapeChannelUrl = ref("")
const scrapeQuery = ref("")
const scrapeSearchMode = ref<"search" | "get_all">("search")
const isScraping = ref(false)
const scrapedItems = ref<Array<Record<string, unknown>>>([])
const testTitles = ref("")
const isTesting = ref(false)
const testResults = ref<TestResult[]>([])

// Предзаполнение поля списком файлов раздачи (окно добавления сериала):
// при появлении/смене списка (смена выбранного качества) обновляем textarea.
if (props.initialTitles?.length) testTitles.value = props.initialTitles.join("\n")
watch(
  () => props.initialTitles,
  (v) => {
    if (v?.length) testTitles.value = v.join("\n")
  },
)

interface MatchEvent { rule?: string; action: string }
interface Extracted {
  season?: number | null
  episode?: number | null
  start?: number | null
  end?: number | null
  part?: number | null
  voiceover?: string | null
  quality?: string | null
  resolution?: string | null
}
interface TestResult {
  source_data: { title: string; resolution?: number | null }
  match_events: MatchEvent[]
  result: { extracted: Extracted; excluded: boolean }
}
interface Pill { key: string; label: string; value: string; icon: string }

function formatResolution(resolution?: number | null): string {
  if (!resolution) return "N/A"
  if (resolution >= 2160) return `4K ${resolution}`
  if (resolution >= 1080) return `FHD ${resolution}`
  if (resolution >= 720) return `HD ${resolution}`
  if (resolution >= 480) return `SD ${resolution}`
  return `${resolution}p`
}

function getResultClass(res: TestResult): string {
  if (!res.match_events || res.match_events.length === 0) return "status-no-match"
  const last = res.match_events[res.match_events.length - 1]
  if (last.action === "exclude") return "status-excluded"
  return "status-success"
}

function isExcluded(res: TestResult): boolean {
  return (res.match_events || []).some((e) => e.action === "exclude")
}
function excludeRuleName(res: TestResult): string {
  // Бэкенд кладёт имя сработавшего правила в поле `rule` (проверено на API).
  return res.match_events.find((e) => e.action === "exclude")?.rule || ""
}

function testResultPills(res: TestResult): Pill[] {
  const e = res?.result?.extracted
  if (!e) return []
  const pills: Pill[] = []
  if (e.season != null) pills.push({ key: "season", label: "Сезон", value: String(e.season), icon: "pi pi-th-large" })
  if (e.episode != null) pills.push({ key: "episode", label: "Серия", value: String(e.episode), icon: "pi pi-video" })
  if (e.start != null) pills.push({ key: "range", label: "Диапазон", value: `${e.start}-${e.end}`, icon: "pi pi-list" })
  if (e.part != null) pills.push({ key: "part", label: "Часть", value: String(e.part), icon: "pi pi-clone" })
  if (e.voiceover) pills.push({ key: "voiceover", label: "Тег", value: String(e.voiceover), icon: "pi pi-tags" })
  if (e.quality) pills.push({ key: "quality", label: "Качество", value: String(e.quality), icon: "pi pi-star" })
  if (e.resolution) pills.push({ key: "resolution", label: "Разрешение", value: String(e.resolution), icon: "pi pi-arrows-h" })
  return pills
}

const isWebUrl = computed(() => {
  const url = scrapeChannelUrl.value.trim().toLowerCase()
  return (url.startsWith("http://") || url.startsWith("https://")) &&
    !url.includes("vk.com") && !url.includes("vkvideo.ru")
})

const isDbId = computed(() => {
  return /^ID\d+$/i.test(scrapeChannelUrl.value.trim())
})

const scrapeIcon = computed(() => {
  if (isDbId.value) return "pi pi-database"
  if (isWebUrl.value) return "pi pi-globe"
  return "pi pi-youtube"
})

const fetchButtonLabel = computed(() => {
  if (isDbId.value) return "Загрузить из БД"
  if (isWebUrl.value) return "Получить с сайта"
  return "Получить с VK"
})

const fetchButtonIcon = computed(() => {
  if (isScraping.value) return "pi-spin pi-spinner"
  if (isDbId.value) return "pi-database"
  if (isWebUrl.value) return "pi-globe"
  return "pi-cloud-download"
})

async function scrapeTestTitles(): Promise<void> {
  const input = scrapeChannelUrl.value.trim().toUpperCase()
  const idMatch = input.match(/^ID(\d+)$/)
  if (idMatch) {
    // Загрузка имён файлов сериала из БД по ID#N.
    const seriesId = Number(idMatch[1])
    isScraping.value = true
    toast.add({ severity: "info", summary: "Загрузка", detail: `Имена файлов сериала #${seriesId}…`, life: 2500 })
    try {
      const data = (await request(
        api.GET("/api/series/{series_id}/source-filenames", {
          params: { path: { series_id: seriesId } },
        } as never),
        { errorMessage: "Ошибка загрузки имён" },
      )) as string[] | null
      if (data) {
        scrapedItems.value = []
        testTitles.value = data.join("\n")
        toast.add({ severity: "success", summary: "Готово", detail: `Загружено ${data.length} имён`, life: 2500 })
      }
    } finally {
      isScraping.value = false
    }
    return
  }
  // Скрейп названий с VK-канала или веб-сайта видеопровайдера.
  if (!scrapeChannelUrl.value) return
  isScraping.value = true
  const isWeb = isWebUrl.value
  toast.add({
    severity: "info",
    summary: "Сбор названий",
    detail: isWeb ? "Полный опрос серий с качеством — может занять минуты…" : "Запущен сбор через VK API…",
    life: 2500,
  })
  try {
    const data = (await request(
      api.POST("/api/parser-profiles/scrape-titles", {
        body: {
          channel_url: scrapeChannelUrl.value,
          query: scrapeQuery.value,
          search_mode: scrapeSearchMode.value,
        },
      } as never),
      { errorMessage: "Ошибка сбора названий" },
    )) as Array<Record<string, unknown>> | null
    if (data) {
      scrapedItems.value = data
      testTitles.value = data.map((it) => String(it.title ?? "")).join("\n")
      toast.add({ severity: "success", summary: "Готово", detail: `Собрано ${data.length} записей`, life: 2500 })
    }
  } finally {
    isScraping.value = false
  }
}

async function runTest(): Promise<void> {
  if (!testTitles.value || !props.profileId) return
  isTesting.value = true
  testResults.value = []
  try {
    const currentLines = testTitles.value
      .split("\n")
      .map((t) => t.trim())
      .filter((t) => t !== "")
    const itemsMap = new Map(scrapedItems.value.map((it) => [String(it.title ?? "").trim(), it]))
    const videos = currentLines.map((title) => itemsMap.get(title) || { title })
    const data = (await request(
      api.POST("/api/parser-profiles/test", {
        body: { profile_id: props.profileId, videos },
      } as never),
      { errorMessage: "Ошибка тестирования" },
    )) as TestResult[] | null
    if (data) testResults.value = data
  } finally {
    isTesting.value = false
  }
}
</script>

<template>
  <div class="parser-test">
    <p class="parser-hint">
      Вставьте «сырые» названия видео в поле ниже или загрузите их с сайта/VK, а затем запустите тест.
    </p>

    <div class="modern-fieldset">
      <div class="fieldset-content">
        <label class="modern-label">Режим поиска для теста</label>
        <div class="vk-mode-group">
          <button
            type="button"
            class="vk-mode-btn"
            :class="{ active: scrapeSearchMode === 'search' }"
            @click="scrapeSearchMode = 'search'"
          >
            <i class="pi pi-search" /> Быстрый поиск
          </button>
          <button
            type="button"
            class="vk-mode-btn"
            :class="{ active: scrapeSearchMode === 'get_all' }"
            @click="scrapeSearchMode = 'get_all'"
          >
            <i class="pi pi-list" /> Полное сканирование
          </button>
        </div>
        <small class="fieldset-hint">
          <b>Быстрый поиск:</b> использует API поиска VK. Быстро, но может пропустить некоторые видео.<br />
          <b>Полное сканирование:</b> загружает список всех видео с канала, затем фильтрует. Медленнее, но надёжнее.<br />
          <b>Для сайтов (Ruchime и др.):</b> вставьте ссылку на страницу релиза и нажмите «Получить с сайта».
        </small>
        <div class="field-group mt-3">
          <StGroup>
            <StIcon :icon="scrapeIcon" />
            <StInput v-model="scrapeChannelUrl" label="Ссылка на сайт/VK или ID" />
            <StIcon icon="pi pi-search" />
            <StInput v-model="scrapeQuery" label="Запросы через /" />
            <div class="constructor-item item-button-group">
              <button
                class="btn-icon btn-search btn-text-icon"
                :disabled="!scrapeChannelUrl || isScraping"
                @click="scrapeTestTitles"
              >
                <i class="pi" :class="fetchButtonIcon" />
                <span>{{ fetchButtonLabel }}</span>
              </button>
            </div>
          </StGroup>
        </div>
        <div v-if="isScraping && videoScan.text" class="scan-progress">
          Опрос серий: {{ videoScan.text }}
        </div>
      </div>
    </div>

    <StGroup class="group-auto-height">
      <textarea
        v-model="testTitles"
        class="parser-test-textarea"
        rows="8"
        placeholder="Название видео 1&#10;Название видео 2"
      />
    </StGroup>

    <div class="parser-test-actions">
      <Button
        label="Запустить тест"
        :icon="isTesting ? 'pi pi-spin pi-spinner' : 'pi pi-play'"
        severity="success"
        :disabled="!testTitles || isTesting"
        @click="runTest"
      />
    </div>

    <div v-if="isTesting" class="parser-loading"><i class="pi pi-spin pi-spinner" style="font-size: 1.6rem" /></div>
    <div v-else class="test-results-container">
      <div
        v-for="(res, index) in testResults"
        :key="index"
        class="card-final card-test-result"
        :class="getResultClass(res)"
      >
        <div class="info-column">
          <div class="card-title-block">
            <span class="card-title" :title="res.source_data.title">{{ res.source_data.title }}</span>
            <div v-if="res.source_data.resolution" class="quality-badge">
              {{ formatResolution(res.source_data.resolution) }}
            </div>
          </div>
        </div>
        <div class="pills-column">
          <div v-if="!res.match_events || res.match_events.length === 0" class="pill">
            <i class="pi pi-ban" /><span>Правила не применились</span>
          </div>
          <div v-else-if="isExcluded(res)" class="pill">
            <i class="pi pi-times-circle" /><span>ИСКЛЮЧЕНО (правило: {{ excludeRuleName(res) }})</span>
          </div>
          <template v-else>
            <div v-for="pill in testResultPills(res)" :key="pill.key" class="pill">
              <i :class="pill.icon" /><span>{{ pill.label }}: <strong>{{ pill.value }}</strong></span>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>
