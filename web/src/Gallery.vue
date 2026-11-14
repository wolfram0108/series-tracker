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
import FoldButton from "./components/FoldButton.vue"
import InfoTile from "./components/InfoTile.vue"
import SheetRow from "./components/SheetRow.vue"
import StControl from "./components/StControl.vue"
import StLabel from "./components/StLabel.vue"
import StValue from "./components/StValue.vue"
import VoiceOrder from "./components/status/VoiceOrder.vue"
import QueuePanel from "./components/status/output/QueuePanel.vue"
import ReadProgress from "./components/status/output/ReadProgress.vue"
import SeriesCard from "./components/SeriesCard.vue"
import Toast from "primevue/toast"
import { t, tAround, tn } from "./i18n"
import { SERIES_STATES, stateLabel, stateWord } from "./lib/states"
import {
  DISABLED_SAMPLE, EXCLUDED_BY_RULE, FRONT_EPISODE_TITLE, LOGS, MISSING_EPISODE, PASSWORD_SAMPLE,
  PROFILE_ANIME, PROFILE_DEFAULT, PROFILE_MOVIES, QUEUE_DOWNLOAD, QUEUE_MONITOR, QUEUE_PROCESSING,
  QUEUE_SLICING, RICK_EPISODE_FILE, RICK_EPISODE_TITLE, RICK_RECEIVED_FILE,
  RICK_SLICED_FILE, RICK_SLICED_TITLE, RICK_TEST_APPLIED, RICK_TEST_EXCLUDED, ROOT_PATH, ROWS,
  SERIES_FRONT, SERIES_FROM, SERIES_STRANGE, SLICING_SAMPLE,
  CHIP_FILE, READS, SAME_HELD, SAME_MARKS, SAME_QUEUE,
} from "./gallery.fixtures"

// Карточки сериала — по состояниям, по порядку.
const cards = [
  { id: 1, name: t("gallery.card.stateEmpty", { state: stateLabel(SERIES_STATES.waiting) }), site: "kinozal.me", auto_scan_enabled: true, statuses: ["waiting"] },
  { id: 2, name: stateLabel(SERIES_STATES.ready), site: "kinozal.me", auto_scan_enabled: true, statuses: ["ready"], last_scan_time: "18.06, 00:34", tmdb: { downloaded: 10, total: 10 } },
  { id: 3, name: SERIES_FRONT, site: "kinozal.me", auto_scan_enabled: true, statuses: ["downloading"], last_scan_time: "18.06, 00:31", tmdb: { downloaded: 5, total: 12 } },
  { id: 4, name: t("gallery.card.stateNoPeers", { state: stateLabel(SERIES_STATES.idle) }), site: "rutracker", auto_scan_enabled: false, statuses: ["idle"], last_scan_time: "18.06, 00:30" },
  { id: 5, name: stateLabel(SERIES_STATES.queued), site: "rutracker", auto_scan_enabled: true, statuses: ["queued"] },
  { id: 6, name: stateLabel(SERIES_STATES.scanning), site: "anilibria", auto_scan_enabled: true, statuses: ["scanning"] },
  { id: 7, name: stateLabel(SERIES_STATES.error), site: "kinozal.me", auto_scan_enabled: true, statuses: ["error"] },
  { id: 8, name: t("gallery.card.severalStatuses"), site: "vk_video", auto_scan_enabled: true, statuses: ["scanning", "downloading", "metadata"] },
  { id: 9, name: t("gallery.card.mixedVk", { first: stateLabel(SERIES_STATES.ready), second: stateWord(SERIES_STATES.waiting) }), site: "vk_video", auto_scan_enabled: true, statuses: ["ready", "waiting"], tmdb: { downloaded: 7, total: 24 } },
  { id: 10, name: stateLabel("slice"), site: "kinozal.me", auto_scan_enabled: true, statuses: ["slicing"] },
  { id: 11, name: t("gallery.card.alignAndPack"), site: "vk_video", auto_scan_enabled: true, statuses: ["scanning", "aligning", "packing", "downloading"] },
]

// Симуляция анимаций карточки: смена статусов → перетекание слоёв (0.8s),
// fade пилюль (badge-fade), смена скорости полос. Эталон — старый фронт.
const simStates: Record<string, string[]> = {
  [stateLabel(SERIES_STATES.waiting)]: ["waiting"],
  [stateLabel(SERIES_STATES.scanning)]: ["scanning"],
  [stateLabel("slice")]: ["slicing"],
  [stateLabel("align")]: ["aligning"],
  [stateLabel("pack")]: ["packing"],
  [stateLabel(SERIES_STATES.downloading)]: ["downloading"],
  [stateLabel(SERIES_STATES.idle)]: ["idle"],
  [stateLabel(SERIES_STATES.queued)]: ["queued"],
  [stateLabel(SERIES_STATES.metadata)]: ["metadata"],
  [stateLabel(SERIES_STATES.ready)]: ["ready"],
  [t("gallery.sim.multi")]: ["scanning", "downloading", "metadata"],
  [stateLabel(SERIES_STATES.error)]: ["error"],
}
const simSeries = reactive({
  id: 42, name: t("gallery.sim.seriesName"), site: "kinozal.me",
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
const text = ref(SERIES_STRANGE)
const num = ref(2)
const sel = ref(null)
const selOptions = [
  { label: PROFILE_DEFAULT, value: 1 },
  { label: PROFILE_ANIME, value: 2 },
]
const sw = ref(true)
const check = ref(true)
const mode = ref("search")
const modeOptions = [
  { label: t("gallery.mode.search"), value: "search" },
  { label: t("gallery.mode.getAll"), value: "get_all" },
]
const rowsView = ROWS.map((r) => ({ ...r, status: stateLabel(r.status) }))

// Карточки-сущности (Композиция / тест VK / TMDB) — единый стиль .card-final.
// TMDB-результаты (список + выбранный)
const tmdbResults = [
  { name: SERIES_STRANGE, original: "Stranger Things", year: 2016, id: 66732, selected: false },
  { name: SERIES_FROM, original: "From", year: 2022, id: 122226, selected: true },
]
// Композиция торрента
const torrentFiles = [
  {
    title: FRONT_EPISODE_TITLE, res: "1080p",
    got: "Kekkai.Sensen.S01E01.Secret.Society.avi", actual: "Kekkai Sensen s01e01.avi",
    pills: [
      { icon: "pi-check-square", label: t("common.status"), value: "renamed" },
      { icon: "pi-tags", label: t("gallery.pill.tag"), value: "AniLibria" },
      { icon: "pi-video", label: t("common.quality"), value: "WEB-DL" },
      { icon: "pi-id-card", label: "ID", value: "1 / 974f83ee" },
    ],
  },
]
// Композиция VK (compilation) — со свитчом
const compFiles = [
  {
    title: RICK_EPISODE_TITLE, res: "720p",
    got: RICK_RECEIVED_FILE, actual: RICK_EPISODE_FILE,
    will: RICK_EPISODE_FILE, enabled: true,
    pills: [
      { icon: "pi-calendar", label: t("gallery.pill.plan"), value: "scheduled" },
      { icon: "pi-tags", label: t("gallery.pill.tag"), value: "AniLibria" },
      { icon: "pi-check-circle", label: t("common.status"), value: "renamed" },
      { icon: "pi-id-card", label: "ID", value: "a1b2c3d4" },
    ],
  },
]
// Тест правил VK — результат (применилось / исключено)
const testResults = [
  { title: RICK_TEST_APPLIED, res: "1080p", status: "success",
    pills: [{ icon: "pi-tags", label: t("rulesTest.pill.voiceover"), value: "AniLibria" }, { icon: "pi-hashtag", label: t("rulesTest.pill.episode"), value: "1" }] },
  { title: RICK_TEST_EXCLUDED, res: "1080p", status: "excluded",
    pills: [{ icon: "pi-times-circle", label: t("gallery.test.excluded"), value: EXCLUDED_BY_RULE }] },
]

// Шапка вкладок настроек — SelectButton (сегмент, адаптивнее папочек)
const settingsTab = ref("auth")
const settingsTabs = [
  { label: t("settings.tab.auth"), value: "auth", icon: "pi-key" },
  { label: t("settings.tab.sources"), value: "sources", icon: "pi-wifi" },
  { label: t("rules.tab"), value: "parser", icon: "pi-filter" },
  { label: t("settings.tab.agents"), value: "agents", icon: "pi-server" },
  { label: t("settings.tab.debug"), value: "debug", icon: "pi-wrench" },
]
// The words around the name of the open tab, which the body shows in bold.
const settingsBody = tAround("gallery.settings.body", "tab")
// The widths (rem) of the rooms the sheet is shown in: its caption stands beside its body where both fit and above it where not.
const SHEET_ROOMS = [26, 40, 64]
// The rooms (rem) of the chips of the tracks, and the tracks: whole, with one gap, with many gaps, one episode, a long name.
const VOICE_ROOMS = [24, 40, 72]
const VOICE_SAMPLE = [
  { label: "ruchime", episodes: "1–154, 158–162, 165–168, 170–174, 176–656", online: true },
  { label: "PVA", episodes: "1–525, 527–656", online: true },
  { label: "AnimeVost", episodes: "509", online: true },
  { label: "Original", episodes: "0–656", online: true },
  { label: "Dub", title: "Dub with a very long name the user gave to the track in the containers", online: false,
    episodes: "1–12, 14–19, 21–23, 25, 27–31, 33–39, 41–44, 46, 48–52, 54–57, 59–63, 65–70, 72, 74–81, 83–90, 92–97, 99–104, 106–111" },
]
const sheetUrl = ref("http://127.0.0.1:5100")
const sheetOn = ref(true)
const sheetNote = (sources: number, lines: number) =>
  t("extensions.chines.appNote", { sources: tn("extensions.chines.sources", sources), lines: tn("extensions.chines.lines", lines) })
const SHEET_TILES = [{ title: "半日 Banri", note: sheetNote(10, 7) }, { title: "橘汁 Juzi", note: sheetNote(8, 6) },
  { title: "米诺 AppMN", note: sheetNote(2, 2) }, { title: "139云盘 139 Cloud", note: sheetNote(0, 2) }]

// The widths (rem) of the rooms the folding button is shown in: it folds at the tier it names (30, 42, 56, 66 rem).
const FOLD_ROOMS = [24, 36, 48, 60, 72]
const HEAD_ROOMS = [24, 40, 72]
const READ_ROOMS = [24, 48]
const SAME_ROOMS = [72, 36]
function settingsLabel(): string {
  return settingsTabs.find((tab) => tab.value === settingsTab.value)?.label ?? ""
}

// StField (floating-label, порт constructor-group)
const fText = ref("")
const fPath = ref(ROOT_PATH)
const fPwd = ref(PASSWORD_SAMPLE)

// Нагруженные примеры из прода
const profileSel = ref<number | null>(null)
const profileOptions = [
  { label: PROFILE_ANIME, value: 1 },
  { label: PROFILE_MOVIES, value: 2 },
]
const newProfile = ref("")
const rtLogin = ref("bitter194")
const rtPass = ref("password123")
</script>

<template>
  <main class="gallery">
    <Toast />
    <header>
      <h1>{{ t("gallery.heading") }} <small>{{ t("gallery.heading.version") }}</small></h1>
      <p class="muted">{{ t("gallery.intro") }}</p>
    </header>

    <section>
      <h2>{{ t("gallery.cards.h2") }}</h2>
      <SeriesCard v-for="c in cards" :key="c.id" :series="c" />
    </section>

    <section>
      <h2>{{ t("gallery.sim.h2") }}</h2>
      <p class="muted">{{ t("gallery.sim.hint") }}</p>
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
        <Button :label="playing ? t('gallery.sim.stop') : t('gallery.sim.run')" size="small" @click="togglePlay" />
      </div>
    </section>

    <section>
      <h2>{{ t("gallery.unified.h2") }}</h2>
      <p class="muted">{{ t("gallery.unified.hint") }}</p>

      <h3 class="sub">{{ t("gallery.sub.tmdb") }}</h3>
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
              <span class="path-pill"><span class="path-pill-label">{{ t("seriesForm.originalLabel") }}</span><span class="path-pill-value">{{ r.original }}</span></span>
            </div>
          </div>
          <div class="pills-column">
            <div class="quality-badge">ID: {{ r.id }}</div>
            <div v-if="r.selected" class="pill"><i class="pi pi-check-circle"></i><span>{{ t("seriesForm.chosen") }}</span></div>
          </div>
        </div>
      </div>

      <h3 class="sub">{{ t("gallery.sub.torrent") }}</h3>
      <div class="composition-cards-container">
        <div v-for="f in torrentFiles" :key="f.title" class="card-final card-torrent status-success">
          <div class="info-column">
            <div class="card-title-block">
              <span class="card-title">{{ f.title }}</span>
              <div class="quality-badge">{{ f.res }}</div>
            </div>
            <div class="path-line"><span class="path-pill"><span class="path-pill-label">{{ t("composition.label.received") }}</span><span class="path-pill-value">{{ f.got }}</span></span></div>
            <div class="path-line"><span class="path-pill"><span class="path-pill-label">{{ t("composition.label.actual") }}</span><span class="path-pill-value">{{ f.actual }}</span></span></div>
          </div>
          <div class="pills-column">
            <div v-for="p in f.pills" :key="p.label" class="pill"><i class="pi" :class="p.icon"></i><span>{{ p.label }}: <strong>{{ p.value }}</strong></span></div>
          </div>
        </div>
      </div>

      <h3 class="sub">{{ t("gallery.sub.vk") }}</h3>
      <div class="composition-cards-container">
        <div v-for="f in compFiles" :key="f.title" class="card-final card-compilation status-pending">
          <div class="info-column">
            <div class="card-title-block">
              <span class="card-title">{{ f.title }}</span>
              <div class="quality-badge">{{ f.res }}</div>
            </div>
            <div class="path-line"><span class="path-pill"><span class="path-pill-label">{{ t("composition.label.received") }}</span><span class="path-pill-value">{{ f.got }}</span></span></div>
            <div class="path-line"><span class="path-pill"><span class="path-pill-label">{{ t("composition.label.actual") }}</span><span class="path-pill-value">{{ f.actual }}</span></span></div>
            <div class="path-line"><span class="path-pill is-mismatch"><span class="path-pill-label">{{ t("composition.label.will") }}</span><span class="path-pill-value">{{ f.will }}</span></span></div>
          </div>
          <div class="pills-column">
            <div v-for="p in f.pills" :key="p.label" class="pill"><i class="pi" :class="p.icon"></i><span>{{ p.label }}: <strong>{{ p.value }}</strong></span></div>
          </div>
          <div class="controls-column">
            <label class="switch"><input type="checkbox" :checked="f.enabled" /><span class="slider"></span></label>
          </div>
        </div>
      </div>

      <h3 class="sub">{{ t("gallery.sub.sliced") }}</h3>
      <div class="composition-cards-container">
        <div class="card-final card-sliced">
          <div class="info-column">
            <div class="card-title-block">
              <span class="card-title">{{ RICK_SLICED_TITLE }}</span>
              <div class="quality-badge">720p</div>
            </div>
            <div class="path-line"><span class="path-pill"><span class="path-pill-label">{{ t("gallery.path.parent") }}</span><span class="path-pill-value">{{ SLICING_SAMPLE.title }}</span></span></div>
            <div class="path-line"><span class="path-pill"><span class="path-pill-label">{{ t("composition.label.actual") }}</span><span class="path-pill-value">{{ RICK_SLICED_FILE }}</span></span></div>
          </div>
          <div class="pills-column">
            <div class="pill"><i class="pi pi-images"></i><strong>{{ t("gallery.sliced.label") }}</strong></div>
            <div class="pill"><i class="pi pi-check-circle"></i><span>{{ t("gallery.sliced.onPlace") }}</span></div>
          </div>
        </div>
        <div class="card-final card-missing">
          <span class="card-title">{{ t("gallery.missing.label", { code: MISSING_EPISODE }) }}</span>
          <i class="pi pi-eye-slash missing-icon"></i>
        </div>
      </div>

      <h3 class="sub">{{ t("gallery.sub.test") }}</h3>
      <div class="composition-cards-container">
        <div v-for="item in testResults" :key="item.title" class="card-final card-test-result" :class="`status-${item.status}`">
          <div class="info-column">
            <div class="card-title-block">
              <span class="card-title">{{ item.title }}</span>
              <div class="quality-badge">{{ item.res }}</div>
            </div>
          </div>
          <div class="pills-column">
            <div v-for="p in item.pills" :key="p.label" class="pill"><i class="pi" :class="p.icon"></i><span>{{ p.label }}: <strong>{{ p.value }}</strong></span></div>
          </div>
        </div>
      </div>

      <h3 class="sub">{{ t("gallery.sub.slicing") }}</h3>
      <div class="slicing-card slicing-card-accent status-success">
        <div class="slicing-card-header">
          <strong class="compilation-title">{{ SLICING_SAMPLE.title }}</strong>
          <div style="display: flex; gap: 8px">
            <button class="control-btn" :title="t('slicing.check')"><i class="pi pi-search"></i></button>
            <button class="control-btn text-info" :title="t('slicing.checkFilter')"><i class="pi pi-filter"></i></button>
            <button class="control-btn text-primary" :title="t('gallery.slicing.cut')"><i class="pi pi-clone"></i></button>
            <button class="control-btn text-danger" :title="t('gallery.slicing.deleteSource')"><i class="pi pi-trash"></i></button>
          </div>
        </div>
        <div class="slicing-card-body">
          <div class="chapter-section">
            <h6>{{ t("slicing.chapters.active", { count: SLICING_SAMPLE.active.length }) }}</h6>
            <div class="chapter-list">
              <span v-for="c in SLICING_SAMPLE.active" :key="c" class="chapter-pill chapter-active">{{ c }}</span>
            </div>
          </div>
          <div class="chapter-section">
            <h6>{{ t("slicing.chapters.garbage", { count: SLICING_SAMPLE.garbage.length }) }}</h6>
            <div class="chapter-list">
              <span v-for="c in SLICING_SAMPLE.garbage" :key="c" class="chapter-pill chapter-garbage">{{ c }} <i class="pi pi-times-circle"></i></span>
            </div>
          </div>
        </div>
        <div class="slicing-card-footer card-footer-status">
          <span>{{ SLICING_SAMPLE.status }}</span>
        </div>
      </div>
    </section>

    <section>
      <h2>{{ t("gallery.queues.h2") }}</h2>
      <p class="muted">{{ t("gallery.queues.hint") }}</p>

      <h3 class="sub">{{ t("agents.processing.title") }}</h3>
      <div class="composition-cards-container">
        <div v-for="q in QUEUE_PROCESSING" :key="q.hash" class="card-final card-queue">
          <div class="queue-row">
            <span class="queue-title">{{ q.title }}</span>
            <div class="pill"><i class="pi pi-link"></i> {{ t("agents.torrent", { id: q.torrent }) }}</div>
            <div class="pill"><i class="pi pi-key"></i> {{ q.hash }}…</div>
            <div class="pill pill-info">{{ stateWord(q.stage) }}</div>
          </div>
        </div>
      </div>

      <h3 class="sub">{{ t("download.title") }}</h3>
      <div class="composition-cards-container">
        <div v-for="q in QUEUE_DOWNLOAD" :key="q.file" class="card-final card-queue">
          <div class="queue-row">
            <span class="queue-title">{{ q.file }}</span>
            <div class="pill" :class="q.cls">{{ stateWord(q.status) }}</div>
            <button class="btn-cancel" :title="t('gallery.queue.stopDownload')"><i class="pi pi-times"></i></button>
          </div>
          <div class="queue-row">
            <div class="progress">
              <div class="progress-bar progress-bar-striped progress-bar-animated" :class="{ 'bg-info': q.remux }" :style="{ width: q.progress + '%' }">{{ q.progress }}%</div>
            </div>
            <div class="queue-metrics">
              <span v-if="q.speed" class="m-speed">↓{{ q.speed }}</span>
              <span v-if="q.remux" class="m-size">remux</span>
              <span class="m-eta">ETA {{ q.eta }}</span>
            </div>
          </div>
        </div>
      </div>

      <h3 class="sub">{{ t("agents.slicing.title") }}</h3>
      <div class="composition-cards-container">
        <div v-for="q in QUEUE_SLICING" :key="q.title" class="card-final card-queue">
          <div class="queue-row">
            <span class="queue-title">{{ q.title }}</span>
            <div class="pill pill-info">{{ stateLabel(q.status) }}</div>
          </div>
          <div class="queue-row">
            <div class="progress">
              <div class="progress-bar progress-bar-striped progress-bar-animated" :style="{ width: (q.done / q.total * 100) + '%' }">{{ t("gallery.queue.chapters", { done: q.done, total: q.total }) }}</div>
            </div>
          </div>
        </div>
      </div>

      <h3 class="sub">{{ t("agents.torrents.title") }}</h3>
      <div class="composition-cards-container">
        <div v-for="q in QUEUE_MONITOR" :key="q.hash" class="card-final card-queue">
          <div class="queue-row">
            <span class="queue-title">{{ q.title }}</span>
            <div class="pill"><i class="pi pi-key"></i> {{ q.hash }}…</div>
            <div class="pill pill-primary">{{ stateLabel(q.status) }}</div>
          </div>
          <div class="queue-row">
            <div class="progress">
              <div class="progress-bar progress-bar-striped progress-bar-animated" :style="{ width: q.progress + '%' }">{{ q.progress }}%</div>
            </div>
            <div class="queue-metrics">
              <span class="m-speed">↓{{ q.speed }}</span>
              <span class="m-eta">ETA {{ q.eta }}</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section>
      <h2>{{ t("gallery.fields.h2") }}</h2>
      <div class="grid">
        <div class="cell"><label>{{ t("gallery.field.text") }}</label><InputText v-model="text" /></div>
        <div class="cell"><label>{{ t("gallery.field.textInvalid") }}</label><InputText v-model="text" invalid /></div>
        <div class="cell"><label>{{ t("gallery.field.textDisabled") }}</label><InputText :model-value="DISABLED_SAMPLE" disabled /></div>
        <div class="cell"><label>{{ t("gallery.field.number") }}</label><InputNumber v-model="num" show-buttons :min="0" :max="8" /></div>
        <div class="cell"><label>Select</label><Select v-model="sel" :options="selOptions" option-label="label" :placeholder="t('sources.profile.label')" /></div>
      </div>
    </section>

    <section>
      <h2>{{ t("gallery.float.h2") }}</h2>
      <div class="grid">
        <div class="cell"><StField v-model="fText" :label="t('seriesForm.nameRu')" /></div>
        <div class="cell"><StField v-model="fPath" :label="t('seriesForm.savePath')" icon="pi pi-folder-open" /></div>
        <div class="cell"><StField v-model="fPwd" :label="t('auth.password')" type="password" icon="pi pi-lock" /></div>
        <div class="cell"><StField model-value="" :label="t('gallery.float.invalid')" icon="pi pi-tag" state="invalid" /></div>
      </div>
    </section>

    <section>
      <h2>{{ t("gallery.toggles.h2") }}</h2>
      <div class="grid">
        <div class="cell"><label>ToggleSwitch</label><ToggleSwitch v-model="sw" /></div>
        <div class="cell"><label>Checkbox</label><Checkbox v-model="check" binary /></div>
        <div class="cell">
          <label>{{ t("gallery.toggles.selectButton") }}</label>
          <SelectButton v-model="mode" :options="modeOptions" option-label="label" option-value="value" />
        </div>
      </div>
    </section>

    <section>
      <h2>{{ t("gallery.buttons.h2") }}</h2>
      <div class="row">
        <Button :label="t('common.save')" icon="pi pi-check" />
        <Button :label="t('common.close')" severity="secondary" icon="pi pi-times" />
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
      <h2>{{ t("gallery.fold.h2") }}</h2>
      <p class="muted small">{{ t("gallery.fold.hint") }}</p>
      <div v-for="width in FOLD_ROOMS" :key="width" class="fold-demo">
        <span class="muted small">{{ t("gallery.fold.room", { width }) }}</span>
        <div class="fold-room fold-demo-room" :style="{ width: `${width}rem` }">
          <div class="row">
            <FoldButton :label="t('output.settings.series')" icon="pi pi-sliders-h" severity="info" />
            <FoldButton :label="t('output.retryFailed')" :hint="t('output.retryFailedHint')" icon="pi pi-undo" severity="secondary" />
            <FoldButton :label="t('output.sync')" icon="pi pi-refresh" severity="warn" />
            <FoldButton :label="t('gallery.fold.late')" :hint="t('gallery.fold.lateHint')" icon="pi pi-check" severity="success" fold="lg" />
            <FoldButton :label="t('gallery.fold.last')" :hint="t('gallery.fold.lastHint')" icon="pi pi-star" severity="info" fold="xl" />
          </div>
        </div>
      </div>
    </section>

    <section class="wide-section">
      <h2>{{ t("gallery.sheet.h2") }}</h2>
      <p class="muted small">{{ t("gallery.sheet.hint") }}</p>
      <div v-for="width in SHEET_ROOMS" :key="width" class="sheet-demo">
        <span class="muted small">{{ t("gallery.fold.room", { width }) }}</span>
        <div class="fold-demo-room" :style="{ width: `${width}rem` }">
          <div class="sheet">
            <SheetRow :caption="t('extensions.sheet.connection')">
              <div class="sheet-pair">
                <StGroup>
                  <StLabel icon="pi pi-link" :text="t('extensions.chines.address')" />
                  <StInput v-model="sheetUrl" :aria-label="t('extensions.chines.address')" />
                </StGroup>
                <StGroup class="is-readonly">
                  <StLabel icon="pi pi-folder" :text="t('extensions.chines.downloadsDir')"><span class="mark">{{ t("extensions.chines.fromConform") }}</span></StLabel>
                  <StValue value="/nas/work/downloads" mono />
                </StGroup>
              </div>
            </SheetRow>
            <SheetRow :caption="t('extensions.sheet.params')">
              <div class="sheet-pair">
                <StGroup>
                  <StLabel :text="t('conform.params.keepTmp')" />
                  <StControl><ToggleSwitch v-model="sheetOn" :aria-label="t('conform.params.keepTmp')" /></StControl>
                </StGroup>
              </div>
            </SheetRow>
            <SheetRow :caption="t('extensions.sheet.apps')" :count="SHEET_TILES.length">
              <div class="tile-grid">
                <InfoTile v-for="tile in SHEET_TILES" :key="tile.title" :title="tile.title" :note="tile.note" />
              </div>
            </SheetRow>
          </div>
        </div>
      </div>
    </section>

    <section class="wide-section">
      <h2>{{ t("gallery.same.h2") }}</h2>
      <p class="muted small">{{ t("gallery.same.hint") }}</p>
      <div v-for="width in SAME_ROOMS" :key="width" class="head-demo-wrap">
        <span class="muted small">{{ t("gallery.fold.room", { width }) }}</span>
        <div class="fold-room fold-demo-room same-demo" :style="{ width: `${width}rem` }">
          <QueuePanel queue="reference" level="episode" :view="SAME_QUEUE" :marks="SAME_MARKS" :held="SAME_HELD" :taken="SAME_HELD" />
        </div>
      </div>
    </section>

    <section class="wide-section">
      <h2>{{ t("gallery.reads.h2") }}</h2>
      <p class="muted small">{{ t("gallery.reads.hint") }}</p>
      <div v-for="width in READ_ROOMS" :key="width" class="head-demo-wrap">
        <span class="muted small">{{ t("gallery.fold.room", { width }) }}</span>
        <div class="fold-room fold-demo-room reads-demo" :style="{ width: `${width}rem` }"><ReadProgress :reads="READS" /></div>
      </div>
      <h3 class="small">{{ t("gallery.chips.h3") }}</h3>
      <div class="row">
        <span class="pill pill-danger"><i class="pi pi-exclamation-circle" aria-hidden="true" />{{ t("output.plan.failuresOpen", { count: 18, open: 1 }) }}</span>
        <span class="pill pill-secondary"><i class="pi pi-exclamation-circle" aria-hidden="true" />{{ t("output.plan.failures", { count: 17 }) }}</span>
        <span class="pill pill-warning"><i class="pi pi-times-circle" aria-hidden="true" />{{ t("output.plan.problems", { count: 3 }) }}</span>
        <span class="pill pill-warning"><i class="pi pi-trash" aria-hidden="true" />{{ t("output.plan.deleteVideo", { name: CHIP_FILE }) }}</span>
      </div>
    </section>

    <section class="wide-section">
      <h2>{{ t("gallery.head.h2") }}</h2>
      <p class="muted small">{{ t("gallery.head.hint") }}</p>
      <div v-for="width in HEAD_ROOMS" :key="width" class="head-demo-wrap">
        <span class="muted small">{{ t("gallery.fold.room", { width }) }}</span>
        <div class="fold-room fold-demo-room" :style="{ width: `${width}rem` }">
          <div class="fold-head head-demo">
            <div class="fold-said">
              <strong>{{ t("gallery.head.title") }}</strong>
              <span class="muted small">{{ t("gallery.head.words") }}</span>
            </div>
            <div class="fold-does">
              <FoldButton :label="t('output.settings.series')" icon="pi pi-sliders-h" severity="info" fold="xl" />
              <FoldButton :label="t('output.sync')" icon="pi pi-refresh" severity="warn" fold="xl" />
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="wide-section voice-samples">
      <h2>{{ t("gallery.voices.h2") }}</h2>
      <p class="muted small">{{ t("gallery.voices.hint") }}</p>
      <div v-for="width in VOICE_ROOMS" :key="width" class="sheet-demo">
        <span class="muted small">{{ t("gallery.fold.room", { width }) }}</span>
        <div class="fold-demo-room" :style="{ width: `${width}rem` }">
          <VoiceOrder :voices="VOICE_SAMPLE" />
        </div>
      </div>
    </section>

    <section class="wide-section">
      <h2>{{ t("gallery.settings.h2") }}</h2>
      <p class="muted small">{{ t("gallery.settings.hint") }}</p>
      <div class="modern-modal">
        <div class="modern-header">
          <h5 class="modal-title"><i class="pi pi-cog"></i> {{ t("settings.title") }}</h5>
          <div class="header-tabs st-tabs">
            <button
              v-for="tab in settingsTabs"
              :key="tab.value"
              class="st-tab"
              :class="{ active: settingsTab === tab.value }"
              :title="tab.label"
              @click="settingsTab = tab.value"
            >
              <i class="pi tab-icon" :class="tab.icon"></i>
              <span class="tab-label" :data-text="tab.label"><span class="tl-text">{{ tab.label }}</span></span>
            </button>
          </div>
          <button class="modern-close" :title="t('common.close')"><i class="pi pi-times"></i></button>
        </div>
        <div class="modern-body">{{ settingsBody[0] }}<strong>{{ settingsLabel() }}</strong>{{ settingsBody[1] }}</div>
        <div class="modern-footer">
          <Button :label="t('common.save')" icon="pi pi-check" />
          <Button :label="t('common.close')" icon="pi pi-times" severity="secondary" />
        </div>
      </div>
    </section>

    <section>
      <h2>{{ t("gallery.pills.h2") }}</h2>
      <div class="grid">
        <div class="cell">
          <label>{{ t("gallery.badge.title") }}</label>
          <div class="row" style="gap: 6px">
            <span class="badge bg-primary">{{ stateWord("download") }}</span>
            <span class="badge bg-info">{{ stateWord("remux") }}</span>
            <span class="badge bg-success">{{ stateWord("done") }}</span>
            <span class="badge bg-danger">{{ stateWord("failed") }}</span>
            <span class="badge bg-secondary">{{ stateWord("planned") }}</span>
            <span class="badge bg-dark">{{ t("add.unknown") }}</span>
          </div>
        </div>
        <div class="cell">
          <label>{{ t("gallery.mirror.title") }}</label>
          <div class="row" style="gap: 8px">
            <span class="mirror-pill">anilibria.top <button class="mirror-pill-remove">&times;</button></span>
            <span class="mirror-pill">aniliberty.top <button class="mirror-pill-remove">&times;</button></span>
          </div>
        </div>
      </div>
      <div class="cell" style="margin-top: 16px">
        <label>{{ t("gallery.vk.title") }}</label>
        <div class="vk-preview">
          <span class="quality-badge">1080p</span>
          <span class="pill"><i class="pi pi-th-large" />{{ t("common.season") }}: <strong>1</strong></span>
          <span class="pill"><i class="pi pi-video" />{{ t("rulesTest.pill.episode") }}: <strong>5</strong></span>
          <span class="pill"><i class="pi pi-tags" />{{ t("gallery.pill.tag") }}: <strong>AniLibria</strong></span>
        </div>
      </div>
    </section>

    <section>
      <h2>{{ t("gallery.tables.h2") }}</h2>
      <p class="muted small">{{ t("gallery.tables.hint") }}</p>
      <div class="fieldset" style="margin-bottom: 20px">
        <div class="fieldset-head"><span>{{ t("download.title") }}</span></div>
        <div class="fieldset-body">
          <DataTable :value="rowsView" size="small">
            <Column field="name" :header="t('gallery.table.seriesOrFile')" />
            <Column field="status" :header="t('common.status')" />
            <Column field="progress" :header="t('gallery.table.progress')" />
          </DataTable>
        </div>
      </div>
      <DataTable :value="LOGS" size="small">
        <Column field="time" :header="t('logs.col.time')" />
        <Column field="group" :header="t('sources.table.group')" />
        <Column field="level" :header="t('logs.col.level')" />
        <Column field="msg" :header="t('logs.col.message')" />
      </DataTable>
    </section>

    <section class="loaded">
      <h2>{{ t("gallery.loaded.h2") }}</h2>
      <p class="muted small">{{ t("gallery.loaded.hint") }}</p>

      <div class="block">
        <p class="muted small">{{ t("gallery.profile.hint") }}</p>
        <StGroup>
          <StIcon icon="pi pi-user-edit" />
          <StSelect v-model="profileSel" :options="profileOptions" :placeholder="t('gallery.profile.placeholder')" />
          <StInput v-model="newProfile" :label="t('rules.profile.new')" />
          <div class="constructor-item item-button-group">
            <StBtn icon="pi pi-plus" variant="add" :title="t('rules.profile.create')" :disabled="!newProfile" />
            <StBtn icon="pi pi-pencil" variant="edit" :title="t('rules.profile.rename')" :disabled="!profileSel" />
            <StBtn icon="pi pi-trash" variant="delete" :title="t('common.delete')" :disabled="!profileSel" />
          </div>
        </StGroup>
      </div>

      <div class="fieldset">
        <div class="fieldset-head"><span>RuTracker.org</span></div>
        <div class="fieldset-body">
          <StGroup>
            <StIcon icon="pi pi-user" />
            <StInput v-model="rtLogin" :label="t('auth.login')" />
            <StIcon icon="pi pi-lock" />
            <StInput v-model="rtPass" :label="t('auth.password')" type="password" />
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
.fold-demo { margin-top: 0.75rem; }
.sheet-demo { margin-top: 0.75rem; }
.head-demo-wrap { margin-top: 0.75rem; }
.head-demo { padding: 0.4rem 0.6rem; background: var(--bg-light); border-radius: 8px; }
.fold-demo-room { max-width: 100%; border: 1px dashed var(--color-gray-200); border-radius: 8px; padding: 0.5rem; margin-top: 0.25rem; }
/* A component whose root is a <section> keeps its own look in a room, not the one of a section of this page. */
.fold-demo-room section { margin-top: 0; border-top: 0; padding-top: 0; }
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
