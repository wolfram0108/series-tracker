<script setup lang="ts">
import { ref, reactive, onUnmounted } from "vue"
import InputText from "primevue/inputtext"
import InputNumber from "primevue/inputnumber"
import Select from "primevue/select"
import ToggleSwitch from "primevue/toggleswitch"
import Checkbox from "primevue/checkbox"
import SelectButton from "primevue/selectbutton"
import Button from "primevue/button"
import DataTable from "primevue/datatable"
import Column from "primevue/column"
import StField from "./components/StField.vue"
import StGroup from "./components/StGroup.vue"
import StIcon from "./components/StIcon.vue"
import StInput from "./components/StInput.vue"
import StSelect from "./components/StSelect.vue"
import StBtn from "./components/StBtn.vue"
import SeriesCard from "./components/SeriesCard.vue"

// Карточки сериала — по состояниям, по порядку.
const cards = [
  { id: 1, name: "Ожидание (пусто)", site: "kinozal.me", auto_scan_enabled: true, statuses: ["waiting"] },
  { id: 2, name: "Готов", site: "kinozal.me", auto_scan_enabled: true, statuses: ["ready"], last_scan_time: "18.06, 00:34", tmdb: { downloaded: 10, total: 10 } },
  { id: 3, name: "Фронт кровавой блокады", site: "kinozal.me", auto_scan_enabled: true, statuses: ["downloading"], last_scan_time: "18.06, 00:31", tmdb: { downloaded: 5, total: 12 } },
  { id: 4, name: "Простой (нет пиров)", site: "rutracker", auto_scan_enabled: false, statuses: ["idle"], last_scan_time: "18.06, 00:30" },
  { id: 5, name: "В очереди", site: "rutracker", auto_scan_enabled: true, statuses: ["queued"] },
  { id: 6, name: "Сканирование", site: "anilibria", auto_scan_enabled: true, statuses: ["scanning"] },
  { id: 7, name: "Ошибка", site: "kinozal.me", auto_scan_enabled: true, statuses: ["error"] },
  { id: 8, name: "Несколько статусов", site: "vk_video", auto_scan_enabled: true, statuses: ["scanning", "downloading", "metadata"] },
  { id: 9, name: "Готов + ожидание (VK)", site: "vk_video", auto_scan_enabled: true, statuses: ["ready", "waiting"], tmdb: { downloaded: 7, total: 24 } },
]

// Симуляция анимаций карточки: смена статусов → перетекание слоёв (0.8s),
// fade пилюль (badge-fade), смена скорости полос. Эталон — старый фронт.
const simStates: Record<string, string[]> = {
  "Ожидание": ["waiting"],
  "Сканирование": ["scanning"],
  "Загрузка": ["downloading"],
  "Простой": ["idle"],
  "В очереди": ["queued"],
  "Метадата": ["metadata"],
  "Готов": ["ready"],
  "Мульти": ["scanning", "downloading", "metadata"],
  "Ошибка": ["error"],
}
const simSeries = reactive({
  id: 42, name: "Симуляция анимаций", site: "kinozal.me",
  auto_scan_enabled: true, statuses: ["waiting"] as string[],
  last_scan_time: "18.06, 00:34", tmdb: { downloaded: 6, total: 12 },
})
function setSim(states: string[]) { simSeries.statuses = states }
const seq = Object.values(simStates)
let simIdx = 0
let simTimer: number | undefined
const playing = ref(false)
function togglePlay() {
  playing.value = !playing.value
  if (playing.value) {
    simTimer = window.setInterval(() => {
      simIdx = (simIdx + 1) % seq.length
      simSeries.statuses = seq[simIdx]
    }, 1500)
  } else {
    clearInterval(simTimer)
  }
}
onUnmounted(() => clearInterval(simTimer))

// Парити-галерея (Ф2). Эталон — старый фронт на «/».
const text = ref("Очень странные дела")
const num = ref(2)
const sel = ref(null)
const selOptions = [
  { label: "Профиль по умолчанию", value: 1 },
  { label: "Аниме", value: 2 },
]
const sw = ref(true)
const check = ref(true)
const mode = ref("search")
const modeOptions = [
  { label: "Быстрый поиск", value: "search" },
  { label: "Полное сканирование", value: "get_all" },
]
const rows = [
  { name: "Фронт кровавой блокады", status: "Загрузка", progress: 45 },
  { name: "Извне", status: "Готов", progress: 100 },
  { name: "Рик и Морти", status: "В очереди", progress: 0 },
]
const logs = [
  { time: "18.06.2026, 00:34:41", group: "gateway", level: "INFO", msg: "SSE клиент подключился (count=1)" },
  { time: "18.06.2026, 00:33:01", group: "https", level: "INFO", msg: "HTTP Request: GET /api/scanner/status \"HTTP/1.1 200 OK\"" },
  { time: "18.06.2026, 00:32:39", group: "torrents", level: "WARNING", msg: "серия 1: торрент отсутствует в qBit — запись сброшена" },
  { time: "18.06.2026, 00:31:10", group: "scan", level: "INFO", msg: "запущено модулей: 13" },
]

// Карточки-сущности (Композиция / тест VK / TMDB) — единый стиль .card-final.
// TMDB-результаты (список + выбранный)
const tmdbResults = [
  { name: "Очень странные дела", original: "Stranger Things", year: 2016, id: 66732, selected: false },
  { name: "Извне", original: "From", year: 2022, id: 122226, selected: true },
]
// Композиция торрента
const torrentFiles = [
  {
    title: "Фронт кровавой блокады s01e01", res: "1080p",
    got: "Kekkai.Sensen.S01E01.Secret.Society.avi", actual: "Kekkai Sensen s01e01.avi",
    pills: [
      { icon: "pi-check-square", label: "Статус", value: "renamed" },
      { icon: "pi-tags", label: "Тег", value: "AniLibria" },
      { icon: "pi-video", label: "Качество", value: "WEB-DL" },
      { icon: "pi-id-card", label: "ID", value: "1 / 974f83ee" },
    ],
  },
]
// Композиция VK (compilation) — со свитчом
const compFiles = [
  {
    title: "Рик и Морти s07e01", res: "720p",
    got: "Рик и Морти 7 сезон 1 серия [AniLibria].mp4", actual: "Рик и Морти s07e01.mkv",
    will: "Рик и Морти s07e01.mkv", enabled: true,
    pills: [
      { icon: "pi-calendar", label: "План", value: "scheduled" },
      { icon: "pi-tags", label: "Тег", value: "AniLibria" },
      { icon: "pi-check-circle", label: "Статус", value: "renamed" },
      { icon: "pi-id-card", label: "ID", value: "a1b2c3d4" },
    ],
  },
]
// Тест правил VK — результат (применилось / исключено)
const testResults = [
  { title: "Рик и Морти 7 сезон 1 серия [AniLibria] 1080p", res: "1080p", status: "success",
    pills: [{ icon: "pi-tags", label: "Озвучка", value: "AniLibria" }, { icon: "pi-hashtag", label: "Серия", value: "1" }] },
  { title: "Рик и Морти 7 сезон [трейлер]", res: "1080p", status: "excluded",
    pills: [{ icon: "pi-times-circle", label: "ИСКЛЮЧЕНО", value: "правило «трейлер»" }] },
]

// Нарезка (slicing-card) — раскрытый список глав
const slicing = {
  title: "Рик и Морти 7 сезон [компиляция].mkv",
  active: ["00:00 (s07e01)", "22:14 (s07e02)", "44:30 (s07e03)"],
  garbage: ["00:00 (Интро)", "21:50 (Реклама блогера)"],
  status: "Готово к нарезке: 3 главы",
}

// Карточки очередей (Агенты) — вместо таблиц
const queueProcessing = [
  { title: "Фронт кровавой блокады", torrent: 12, hash: "974f83ee", stage: "renaming" },
]
const queueDownload = [
  { file: "Рик и Морти s07e01.mp4", status: "загрузка", cls: "pill-primary", progress: 62, speed: "2.1 MB/s", eta: "1:20", remux: false },
  { file: "Извне s01e04.mp4", status: "обработка", cls: "pill-info", progress: 90, speed: "", eta: "0:10", remux: true },
]
const queueSlicing = [
  { title: "Рик и Морти", status: "slicing", done: 2, total: 5 },
]
const queueMonitor = [
  { title: "Извне", hash: "a1b2c3d4", status: "Загрузка", progress: 48, speed: "2.1 MB/s", eta: "1:20" },
]

// Шапка вкладок настроек — SelectButton (сегмент, адаптивнее папочек)
const settingsTab = ref("auth")
const settingsTabs = [
  { label: "Авторизация", value: "auth", icon: "pi-key" },
  { label: "Трекеры", value: "trackers", icon: "pi-wifi" },
  { label: "Фильтры VK", value: "parser", icon: "pi-filter" },
  { label: "Агенты", value: "agents", icon: "pi-server" },
  { label: "Отладка", value: "debug", icon: "pi-wrench" },
]

// StField (floating-label, порт constructor-group)
const fText = ref("")
const fPath = ref("/nas/media/Сериалы")
const fPwd = ref("секрет123")

// Нагруженные примеры из прода
const profileSel = ref<number | null>(null)
const profileOptions = [
  { label: "Аниме", value: 1 },
  { label: "Кино", value: 2 },
]
const newProfile = ref("")
const rtLogin = ref("bitter194")
const rtPass = ref("password123")
</script>

<template>
  <main class="gallery">
    <header>
      <h1>Парити-галерея <small>/v2 · Ф2</small></h1>
      <p class="muted">PrimeVue + StField (порт constructor-group) под токены series-tracker. Эталон — старый фронт на «/».</p>
    </header>

    <section>
      <h2>Карточки сериала (кастом-островок) — по состояниям</h2>
      <SeriesCard v-for="c in cards" :key="c.id" :series="c" />
    </section>

    <section>
      <h2>Симуляция анимаций карточки (слои + пилюли + полосы)</h2>
      <p class="muted">Жми сценарии или «Прогон» — слои перетекают (0.8s), пилюли появляются/схлопываются (badge-fade), полосы меняют скорость (2s / 10s / стоп).</p>
      <SeriesCard :series="simSeries" />
      <div class="row" style="margin-top: 12px">
        <Button
          v-for="(states, name) in simStates"
          :key="name"
          :label="name"
          size="small"
          severity="secondary"
          @click="setSim(states)"
        />
        <Button :label="playing ? '⏸ Стоп' : '▶ Прогон'" size="small" @click="togglePlay" />
      </div>
    </section>

    <section>
      <h2>Карточки — единый стиль (.card-final, radius 9px, общая палитра)</h2>
      <p class="muted">Сведены к одному языку: Композиция (торрент/VK/нарезка), тест VK, TMDB. Раньше — разные радиусы (8/10) и фоны.</p>

      <h3 class="sub">TMDB-результат (добавление сериала / инфо)</h3>
      <div class="composition-cards-container">
        <div
          v-for="r in tmdbResults"
          :key="r.id"
          class="card-final card-tmdb"
          :class="r.selected ? 'status-success' : 'status-archived'"
        >
          <div class="info-column">
            <span class="card-title">{{ r.name }} <small style="opacity:.6">({{ r.year }})</small></span>
            <div class="path-line">
              <span class="path-pill"><span class="path-pill-label">Оригинал:</span><span class="path-pill-value">{{ r.original }}</span></span>
            </div>
          </div>
          <div class="pills-column">
            <div class="quality-badge">ID: {{ r.id }}</div>
            <div v-if="r.selected" class="pill"><i class="pi pi-check-circle"></i><span>Выбран</span></div>
          </div>
        </div>
      </div>

      <h3 class="sub">Композиция торрента (Статус → Композиция)</h3>
      <div class="composition-cards-container">
        <div v-for="f in torrentFiles" :key="f.title" class="card-final card-torrent status-success">
          <div class="info-column">
            <div class="card-title-block">
              <span class="card-title">{{ f.title }}</span>
              <div class="quality-badge">{{ f.res }}</div>
            </div>
            <div class="path-line"><span class="path-pill"><span class="path-pill-label">Полученное:</span><span class="path-pill-value">{{ f.got }}</span></span></div>
            <div class="path-line"><span class="path-pill"><span class="path-pill-label">Фактическое:</span><span class="path-pill-value">{{ f.actual }}</span></span></div>
          </div>
          <div class="pills-column">
            <div v-for="p in f.pills" :key="p.label" class="pill"><i class="pi" :class="p.icon"></i><span>{{ p.label }}: <strong>{{ p.value }}</strong></span></div>
          </div>
        </div>
      </div>

      <h3 class="sub">Композиция медиатеки VK (compilation) — со свитчом</h3>
      <div class="composition-cards-container">
        <div v-for="f in compFiles" :key="f.title" class="card-final card-compilation status-pending">
          <div class="info-column">
            <div class="card-title-block">
              <span class="card-title">{{ f.title }}</span>
              <div class="quality-badge">{{ f.res }}</div>
            </div>
            <div class="path-line"><span class="path-pill"><span class="path-pill-label">Полученное:</span><span class="path-pill-value">{{ f.got }}</span></span></div>
            <div class="path-line"><span class="path-pill"><span class="path-pill-label">Фактическое:</span><span class="path-pill-value">{{ f.actual }}</span></span></div>
            <div class="path-line"><span class="path-pill is-mismatch"><span class="path-pill-label">Будет:</span><span class="path-pill-value">{{ f.will }}</span></span></div>
          </div>
          <div class="pills-column">
            <div v-for="p in f.pills" :key="p.label" class="pill"><i class="pi" :class="p.icon"></i><span>{{ p.label }}: <strong>{{ p.value }}</strong></span></div>
          </div>
          <div class="controls-column">
            <label class="switch"><input type="checkbox" :checked="f.enabled" /><span class="slider"></span></label>
          </div>
        </div>
      </div>

      <h3 class="sub">Нарезанный файл (sliced, синий) и Отсутствует (missing)</h3>
      <div class="composition-cards-container">
        <div class="card-final card-sliced">
          <div class="info-column">
            <div class="card-title-block">
              <span class="card-title">Рик и Морти s07e03</span>
              <div class="quality-badge">720p</div>
            </div>
            <div class="path-line"><span class="path-pill"><span class="path-pill-label">Родитель:</span><span class="path-pill-value">Рик и Морти 7 сезон [компиляция].mkv</span></span></div>
            <div class="path-line"><span class="path-pill"><span class="path-pill-label">Фактическое:</span><span class="path-pill-value">Рик и Морти s07e03.mkv</span></span></div>
          </div>
          <div class="pills-column">
            <div class="pill"><i class="pi pi-images"></i><strong>Нарезанный файл</strong></div>
            <div class="pill"><i class="pi pi-check-circle"></i><span>Файл на месте</span></div>
          </div>
        </div>
        <div class="card-final card-missing">
          <span class="card-title">Эпизод s07e05 — не найден в источнике</span>
          <i class="pi pi-eye-slash missing-icon"></i>
        </div>
      </div>

      <h3 class="sub">Тест правил VK — результат (применилось / исключено)</h3>
      <div class="composition-cards-container">
        <div v-for="t in testResults" :key="t.title" class="card-final card-test-result" :class="`status-${t.status}`">
          <div class="info-column">
            <div class="card-title-block">
              <span class="card-title">{{ t.title }}</span>
              <div class="quality-badge">{{ t.res }}</div>
            </div>
          </div>
          <div class="pills-column">
            <div v-for="p in t.pills" :key="p.label" class="pill"><i class="pi" :class="p.icon"></i><span>{{ p.label }}: <strong>{{ p.value }}</strong></span></div>
          </div>
        </div>
      </div>

      <h3 class="sub">Нарезка (slicing-card) — раскрытый список глав</h3>
      <div class="slicing-card slicing-card-accent status-success">
        <div class="slicing-card-header">
          <strong class="compilation-title">{{ slicing.title }}</strong>
          <div style="display: flex; gap: 8px">
            <button class="control-btn" title="Проверить оглавление"><i class="pi pi-search"></i></button>
            <button class="control-btn text-info" title="Проверить и отфильтровать"><i class="pi pi-filter"></i></button>
            <button class="control-btn text-primary" title="Нарезать"><i class="pi pi-clone"></i></button>
            <button class="control-btn text-danger" title="Удалить исходник"><i class="pi pi-trash"></i></button>
          </div>
        </div>
        <div class="slicing-card-body">
          <div class="chapter-section">
            <h6>Активные главы ({{ slicing.active.length }}):</h6>
            <div class="chapter-list">
              <span v-for="c in slicing.active" :key="c" class="chapter-pill chapter-active">{{ c }}</span>
            </div>
          </div>
          <div class="chapter-section">
            <h6>Мусорные главы ({{ slicing.garbage.length }}):</h6>
            <div class="chapter-list">
              <span v-for="c in slicing.garbage" :key="c" class="chapter-pill chapter-garbage">{{ c }} <i class="pi pi-times-circle"></i></span>
            </div>
          </div>
        </div>
        <div class="slicing-card-footer card-footer-status">
          <span>{{ slicing.status }}</span>
        </div>
      </div>
    </section>

    <section>
      <h2>Карточки очередей (Агенты) — вместо таблиц</h2>
      <p class="muted">4 очереди переведены с таблиц на единый карточный вид. Компактно, 1–2 строки, без раздувания.</p>

      <h3 class="sub">Агент Обработки</h3>
      <div class="composition-cards-container">
        <div v-for="t in queueProcessing" :key="t.hash" class="card-final card-queue">
          <div class="queue-row">
            <span class="queue-title">{{ t.title }}</span>
            <div class="pill"><i class="pi pi-link"></i> Торрент: {{ t.torrent }}</div>
            <div class="pill"><i class="pi pi-key"></i> {{ t.hash }}…</div>
            <div class="pill pill-info">{{ t.stage }}</div>
          </div>
        </div>
      </div>

      <h3 class="sub">Агент Загрузки (yt-dlp)</h3>
      <div class="composition-cards-container">
        <div v-for="t in queueDownload" :key="t.file" class="card-final card-queue">
          <div class="queue-row">
            <span class="queue-title">{{ t.file }}</span>
            <div class="pill" :class="t.cls">{{ t.status }}</div>
            <button class="btn-cancel" title="Отменить загрузку"><i class="pi pi-times"></i></button>
          </div>
          <div class="queue-row">
            <div class="progress">
              <div class="progress-bar progress-bar-striped progress-bar-animated" :class="{ 'bg-info': t.remux }" :style="{ width: t.progress + '%' }">{{ t.progress }}%</div>
            </div>
            <div class="queue-metrics">
              <span v-if="t.speed" class="m-speed">↓{{ t.speed }}</span>
              <span v-if="t.remux" class="m-size">remux</span>
              <span class="m-eta">ETA {{ t.eta }}</span>
            </div>
          </div>
        </div>
      </div>

      <h3 class="sub">Агент Нарезки (ffmpeg)</h3>
      <div class="composition-cards-container">
        <div v-for="t in queueSlicing" :key="t.title" class="card-final card-queue">
          <div class="queue-row">
            <span class="queue-title">{{ t.title }}</span>
            <div class="pill pill-info">{{ t.status }}</div>
          </div>
          <div class="queue-row">
            <div class="progress">
              <div class="progress-bar progress-bar-striped progress-bar-animated" :style="{ width: (t.done / t.total * 100) + '%' }">{{ t.done }} / {{ t.total }} глав</div>
            </div>
          </div>
        </div>
      </div>

      <h3 class="sub">Мониторинг торрентов</h3>
      <div class="composition-cards-container">
        <div v-for="t in queueMonitor" :key="t.hash" class="card-final card-queue">
          <div class="queue-row">
            <span class="queue-title">{{ t.title }}</span>
            <div class="pill"><i class="pi pi-key"></i> {{ t.hash }}…</div>
            <div class="pill pill-primary">{{ t.status }}</div>
          </div>
          <div class="queue-row">
            <div class="progress">
              <div class="progress-bar progress-bar-striped progress-bar-animated" :style="{ width: t.progress + '%' }">{{ t.progress }}%</div>
            </div>
            <div class="queue-metrics">
              <span class="m-speed">↓{{ t.speed }}</span>
              <span class="m-eta">ETA {{ t.eta }}</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section>
      <h2>Поля — обычная высота (46px, PrimeVue)</h2>
      <div class="grid">
        <div class="cell"><label>Текст</label><InputText v-model="text" /></div>
        <div class="cell"><label>Текст (invalid)</label><InputText v-model="text" invalid /></div>
        <div class="cell"><label>Текст (disabled)</label><InputText model-value="нельзя" disabled /></div>
        <div class="cell"><label>Число</label><InputNumber v-model="num" show-buttons :min="0" :max="8" /></div>
        <div class="cell"><label>Select</label><Select v-model="sel" :options="selOptions" option-label="label" placeholder="Профиль правил" /></div>
      </div>
    </section>

    <section>
      <h2>Поля floating-label — повышенная высота (64px, StField)</h2>
      <div class="grid">
        <div class="cell"><StField v-model="fText" label="Название (RU)" /></div>
        <div class="cell"><StField v-model="fPath" label="Путь сохранения" icon="pi pi-folder-open" /></div>
        <div class="cell"><StField v-model="fPwd" label="Пароль" type="password" icon="pi pi-lock" /></div>
        <div class="cell"><StField model-value="" label="Имя (invalid)" icon="pi pi-tag" state="invalid" /></div>
      </div>
    </section>

    <section>
      <h2>Переключатели</h2>
      <div class="grid">
        <div class="cell"><label>ToggleSwitch</label><ToggleSwitch v-model="sw" /></div>
        <div class="cell"><label>Checkbox</label><Checkbox v-model="check" binary /></div>
        <div class="cell">
          <label>SelectButton (как btn-group)</label>
          <SelectButton v-model="mode" :options="modeOptions" option-label="label" option-value="value" />
        </div>
      </div>
    </section>

    <section>
      <h2>Кнопки</h2>
      <div class="row">
        <Button label="Сохранить" icon="pi pi-check" />
        <Button label="Закрыть" severity="secondary" icon="pi pi-times" />
        <Button label="Success" severity="success" icon="pi pi-save" />
        <Button label="Danger" severity="danger" icon="pi pi-trash" />
        <Button label="Warn" severity="warn" />
        <Button label="Info" severity="info" />
      </div>
      <div class="row">
        <Button label="Outlined" outlined />
        <Button label="Text" text />
        <Button label="Small" size="small" />
        <Button icon="pi pi-cog" rounded aria-label="cfg" />
      </div>
    </section>

    <section class="wide-section">
      <h2>Окно настроек — шапка с вкладками (адаптивные)</h2>
      <p class="muted small">Целая модалка: шапка (заголовок + вкладки + крестик), тело, футер — единое скруглённое окно. Вкладки интегрированы в шапку и адаптивны (узко → иконки), вместо ломавшихся папочек.</p>
      <div class="modern-modal">
        <div class="modern-header">
          <h5 class="modal-title"><i class="pi pi-cog"></i> Настройки</h5>
          <div class="header-tabs st-tabs">
            <button
              v-for="t in settingsTabs"
              :key="t.value"
              class="st-tab"
              :class="{ active: settingsTab === t.value }"
              :title="t.label"
              @click="settingsTab = t.value"
            >
              <i class="pi tab-icon" :class="t.icon"></i>
              <span class="tab-label">{{ t.label }}</span>
            </button>
          </div>
          <button class="modern-close" title="Закрыть"><i class="pi pi-times"></i></button>
        </div>
        <div class="modern-body">
          Содержимое вкладки «<strong>{{ settingsTabs.find((t) => t.value === settingsTab)?.label }}</strong>» — здесь формы/таблицы соответствующего раздела.
        </div>
        <div class="modern-footer">
          <Button label="Сохранить" icon="pi pi-check" />
          <Button label="Закрыть" icon="pi pi-times" severity="secondary" />
        </div>
      </div>
    </section>

    <section>
      <h2>Пилюли и бейджи — по местам в проде</h2>
      <div class="grid">
        <div class="cell">
          <label>Статус-бейдж очереди (Агенты)</label>
          <div class="row" style="gap: 6px">
            <span class="badge bg-primary">загрузка</span>
            <span class="badge bg-info">обработка</span>
            <span class="badge bg-success">готово</span>
            <span class="badge bg-danger">ошибка</span>
            <span class="badge bg-secondary">ожидание</span>
            <span class="badge bg-dark">неизвестно</span>
          </div>
        </div>
        <div class="cell">
          <label>Зеркало трекера (Трекеры)</label>
          <div class="row" style="gap: 8px">
            <span class="mirror-pill">anilibria.top <button class="mirror-pill-remove">&times;</button></span>
            <span class="mirror-pill">aniliberty.top <button class="mirror-pill-remove">&times;</button></span>
          </div>
        </div>
      </div>
      <div class="cell" style="margin-top: 16px">
        <label>Предпросмотр VK-фильтра (тест правил) — пилюли на цветном фоне</label>
        <div class="vk-preview">
          <span class="quality-badge">1080p</span>
          <span class="pill"><i class="pi pi-th-large" />Сезон: <strong>1</strong></span>
          <span class="pill"><i class="pi pi-video" />Серия: <strong>5</strong></span>
          <span class="pill"><i class="pi pi-tags" />Тег: <strong>AniLibria</strong></span>
        </div>
      </div>
    </section>

    <section>
      <h2>Таблицы (под div-table)</h2>
      <p class="muted small">Очередь — в fieldset с заголовком-баром (как у агентов); ниже — таблица логов.</p>
      <div class="fieldset" style="margin-bottom: 20px">
        <div class="fieldset-head"><span>Очередь Агента Загрузки (yt-dlp)</span></div>
        <div class="fieldset-body">
          <DataTable :value="rows" size="small">
            <Column field="name" header="Сериал / Файл" />
            <Column field="status" header="Статус" />
            <Column field="progress" header="Прогресс, %" />
          </DataTable>
        </div>
      </div>
      <DataTable :value="logs" size="small">
        <Column field="time" header="Время" />
        <Column field="group" header="Группа" />
        <Column field="level" header="Уровень" />
        <Column field="msg" header="Сообщение" />
      </DataTable>
    </section>

    <section class="loaded">
      <h2>Нагруженные примеры из прода (монолитные группы)</h2>
      <p class="muted small">Несколько айтемов соединены в один блок (одна рамка, внутренние разделители) — как в проде.</p>

      <div class="block">
        <p class="muted small">Выберите профиль для редактирования или создайте новый. Правила и тестирование появятся ниже после выбора профиля.</p>
        <StGroup>
          <StIcon icon="pi pi-user-edit" />
          <StSelect v-model="profileSel" :options="profileOptions" placeholder="-- Выберите профиль --" />
          <StInput v-model="newProfile" label="Имя нового профиля..." />
          <div class="constructor-item item-button-group">
            <StBtn icon="pi pi-plus" variant="add" title="Создать" :disabled="!newProfile" />
            <StBtn icon="pi pi-pencil" variant="edit" title="Переименовать" :disabled="!profileSel" />
            <StBtn icon="pi pi-trash" variant="delete" title="Удалить" :disabled="!profileSel" />
          </div>
        </StGroup>
      </div>

      <div class="fieldset">
        <div class="fieldset-head"><span>RuTracker.org</span></div>
        <div class="fieldset-body">
          <StGroup>
            <StIcon icon="pi pi-user" />
            <StInput v-model="rtLogin" label="Логин" />
            <StIcon icon="pi pi-lock" />
            <StInput v-model="rtPass" label="Пароль" type="password" />
          </StGroup>
        </div>
      </div>
    </section>
  </main>
</template>

<style scoped>
.gallery { max-width: 960px; margin: 32px auto; padding: 0 20px 60px; }
h1 { margin-bottom: 4px; }
small { color: #888; font-weight: 400; font-size: 0.6em; }
.muted { color: var(--text-muted); }
.small { font-size: 0.85rem; }
section { margin-top: 28px; border-top: 1px solid var(--color-gray-200); padding-top: 16px; }
/* секция во всю ширину viewport (демо-окно как modal-xl, вне max-width галереи) */
.wide-section {
  width: 100vw; position: relative; left: 50%; transform: translateX(-50%);
  padding: 16px 24px 0; box-sizing: border-box;
}
.wide-section .modern-modal { max-width: 1140px; margin: 0 auto; }
h2 { font-size: 1.05rem; margin-bottom: 12px; }
h3.sub { font-size: 0.85rem; color: var(--color-gray-600); margin: 18px 0 8px; font-weight: 600; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; align-items: start; }
.cell { display: flex; flex-direction: column; gap: 6px; }
.cell > label { font-size: 0.8rem; color: var(--color-gray-600); }
.row { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 10px; }

/* подложка под полупрозрачные пилюли VK-предпросмотра */
.vk-preview {
  display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
  padding: 12px; border-radius: var(--card-border-radius);
  background: linear-gradient(135deg, #009688, #00796D); color: #fff;
}

/* нагруженные примеры */
.loaded .block { margin-bottom: 20px; }
.loaded .row { align-items: center; }
.loaded .row > .input-constructor-group { max-width: 280px; }
.fieldset {
  border: 1px solid var(--color-gray-200);
  border-radius: var(--card-border-radius);
  overflow: hidden;
}
.fieldset-head {
  background: var(--color-gray-100);
  padding: 8px 16px;
  text-align: right;
  font-weight: 600;
  color: var(--color-gray-600);
  border-bottom: 1px solid var(--color-gray-200);
}
.fieldset-body { padding: 16px; }
.fieldset-body.two { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }

</style>
