<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive } from "vue"
import AppHeader from "../components/AppHeader.vue"
import SeriesList from "../components/SeriesList.vue"
import ModalShell from "../components/ModalShell.vue"
import AgentsPanel from "../components/settings/AgentsPanel.vue"
import { t } from "../i18n"
import { useSeriesStore } from "../stores/series"
import { useQueuesStore, type ConformTask, type DownloadCard, type PackCard } from "../stores/queues"
import { simConform, simDownloads, simPack, simSeries } from "./loadSim.fixtures"

// A page on samples (main.ts, #load-sim): the main screen of many series under the open window «Агенты» of many works, every
// card of which changes on every frame of the stream, as the core pushes them (a frame each FRAME_SECONDS of the gateway).
// The load is the same at every run, so a change of the interface is measured against the same load. The address sets it:
// ?series=100&downloads=1000&conform=500&pack=1000&frame=500. The figures of the page are kept in window.__sim.
const query = new URLSearchParams(window.location.search)
const count = (name: string, fallback: number): number => Number(query.get(name) ?? fallback)
const SERIES = count("series", 100)
const DOWNLOADS = count("downloads", 1000)
const CONFORM = count("conform", 500)
const PACK = count("pack", 1000)
const FRAME_MS = count("frame", 500)

const seriesStore = useSeriesStore()
const queues = useQueuesStore()

seriesStore.list = simSeries(SERIES)

// What the page measured: by second, the frames drawn, the longest frame, and the time a frame of the stream took to be
// taken (its text parsed, the queues set, the screen counted).
const sim = reactive({ fps: 0, worst: 0, apply: 0, cards: DOWNLOADS + CONFORM + PACK, seconds: [] as string[] })
;(window as unknown as { __sim: typeof sim }).__sim = sim
let frames = 0, worst = 0, applied = 0, last = performance.now(), running = true
const tick = (now: number): void => {
  frames += 1
  worst = Math.max(worst, now - last)
  last = now
  if (running) requestAnimationFrame(tick)
}
let n = 0
// The beat of the stream is the subject of the page: it is the load it puts on the interface.
const stream = setInterval(() => {
  n += 1
  const text = JSON.stringify({ d: simDownloads(DOWNLOADS, SERIES, n), c: simConform(CONFORM, SERIES, n), p: simPack(PACK, SERIES, n) })
  const start = performance.now()
  const frame = JSON.parse(text) as { d: DownloadCard[]; c: ConformTask[]; p: PackCard[] }
  queues.setDownloads(frame.d)
  queues.setConform({ daemon: { up: true, version: "sim" }, tasks: frame.c })
  queues.setPack(frame.p)
  void Promise.resolve().then(() => Promise.resolve()).then(() => { applied += performance.now() - start })
}, FRAME_MS)
const second = setInterval(() => {
  sim.fps = frames
  sim.worst = Math.round(worst)
  sim.apply = Math.round(applied)
  sim.seconds.push(`${new Date().toLocaleTimeString()} ${frames}fps worst ${Math.round(worst)}ms stream ${Math.round(applied)}ms`)
  if (sim.seconds.length > 600) sim.seconds.shift()
  frames = 0
  worst = 0
  applied = 0
}, 1000)
onMounted(() => requestAnimationFrame(tick))
onBeforeUnmount(() => {
  running = false
  clearInterval(stream)
  clearInterval(second)
})
</script>

<template>
  <div class="sim-page">
    <AppHeader />
    <SeriesList />
    <ModalShell :title="t('loadSim.title', { n: sim.cards })" size="full" fixed-height>
      <AgentsPanel />
    </ModalShell>
    <div class="sim-hud">{{ t("loadSim.hud", { fps: sim.fps, worst: sim.worst, apply: sim.apply }) }}</div>
  </div>
</template>

<style scoped>
.sim-page { padding: 1rem; }
.sim-hud {
  position: fixed; inset-block-start: 0.25rem; inset-inline-start: 0.25rem; z-index: 2000; padding: 0.25rem 0.5rem;
  background: #000; color: #0f0; font: 12px monospace; border-radius: 4px;
}
</style>
