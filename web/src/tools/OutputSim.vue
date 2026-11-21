<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive } from "vue"
import { api } from "../api/client"
import AppHeader from "../components/AppHeader.vue"
import ModalShell from "../components/ModalShell.vue"
import SeriesList from "../components/SeriesList.vue"
import StatusOutput from "../components/status/StatusOutput.vue"
import { t } from "../i18n"
import { usePlanStore } from "../stores/plan"
import { useQueuesStore, type ConformTask, type DownloadCard, type PackCard } from "../stores/queues"
import { useSeriesStore } from "../stores/series"
import { simConform, simDownloads, simPack, simSeries } from "./loadSim.fixtures"
import { SIM_SERIES, simComposition, simPlan, simQueues } from "./outputSim.fixtures"

// A page on samples (main.ts, #output-sim): the window «Выход» of a series of many episodes over the main screen, the works of
// that series changing on every frame of the stream, and a fixed round of what a user does in it — scroll, select, switch
// episodes off and on, open them. Every request of the window is answered here, so nothing reaches a server; a switch is
// answered as the core answers it, by a new plan of the whole series. The address sets the load:
// ?episodes=1000&series=100&works=300&frame=500. The figures of the round are kept in window.__out.
const query = new URLSearchParams(window.location.search)
const count = (name: string, fallback: number): number => Number(query.get(name) ?? fallback)
const EPISODES = count("episodes", 1000)
const SERIES = count("series", 100)
const WORKS = count("works", 300)
const FRAME_MS = count("frame", 500)

const plan = usePlanStore()
const queues = useQueuesStore()
useSeriesStore().list = simSeries(SERIES)

const off = new Set<number>()
const json = (data: unknown): Response => new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } })
api.use({
  async onRequest({ request, schemaPath }) {
    const method = request.method
    if (method === "GET" && schemaPath === "/api/series/{series_id}/composition") return json(simComposition(EPISODES))
    if (method === "GET" && schemaPath === "/api/series/{series_id}/plan") return json(simPlan(EPISODES, off))
    if (method === "GET" && schemaPath === "/api/series/{series_id}/output/queues") {
      return json(simQueues(Number(new URL(request.url).searchParams.get("episode") ?? 1)))
    }
    if (method === "PUT" && schemaPath === "/api/series/{series_id}/output/switch") {
      const body = await request.clone().json() as { episode?: number | null; off?: boolean }
      if (body.episode != null) {
        if (body.off) off.add(body.episode)
        else off.delete(body.episode)
      }
      // The core answers a switch with a new plan of the whole series.
      queueMicrotask(() => plan.apply(simPlan(EPISODES, off)))
      return json({})
    }
    return json({})
  },
})

// The works of the series: their states move, so the cards of the episodes they are for change their colour.
const moved = <T extends { status: string }>(cards: T[], tick: number, states: string[]): T[] =>
  cards.map((card, at) => ({ ...card, status: states[(at + tick) % states.length] }))
const sim = reactive({ fps: 0, worst: 0, apply: 0, phase: "", phases: [] as string[] })
;(window as unknown as { __out: typeof sim & { run: () => Promise<string[]> } }).__out = Object.assign(sim, { run })
let frames = 0, worst = 0, slow = 0, applied = 0, last = performance.now(), running = true
const tick = (now: number): void => {
  frames += 1
  const took = now - last
  worst = Math.max(worst, took)
  if (took > 50) slow += 1
  last = now
  if (running) requestAnimationFrame(tick)
}
let n = 0
// The beat of the stream is the subject of the page: it is the load it puts on the interface.
const stream = setInterval(() => {
  n += 1
  const text = JSON.stringify({ d: moved(simDownloads(WORKS, 1, n), n, ["queued", "download", "done"]),
    c: moved(simConform(WORKS, 1, n), n, ["queued", "running", "done"]), p: moved(simPack(WORKS, 1, n), n, ["queued", "build", "done"]) })
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
  frames = 0
  worst = 0
  applied = 0
}, 1000)

const pause = (ms: number) => new Promise((done) => setTimeout(done, ms))
const cards = (): HTMLElement[] => [...document.querySelectorAll<HTMLElement>(".episode-card:not(.card-shell)")]
const scroller = (): HTMLElement | null => document.querySelector<HTMLElement>(".modern-body")
const click = (element: Element, init: MouseEventInit = {}) =>
  element.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, detail: 1, ...init }))

// One step of a phase each `every` ms for `ms`; the frames of the phase are counted from its first step to its end.
async function phase(name: string, ms: number, every: number, step: (at: number) => void): Promise<string> {
  sim.phase = name
  const begin = performance.now()
  const framesBefore = frames
  frames = 0; worst = 0; slow = 0
  let total = 0, worstAll = 0, slowAll = 0
  const counter = setInterval(() => { total += frames; worstAll = Math.max(worstAll, worst); slowAll += slow; frames = 0; worst = 0; slow = 0 }, 250)
  for (let at = 0; performance.now() - begin < ms; at += 1) {
    step(at)
    await pause(every)
  }
  clearInterval(counter)
  total += frames; worstAll = Math.max(worstAll, worst); slowAll += slow
  frames = framesBefore
  const seconds = (performance.now() - begin) / 1000
  return `${name}: ${Math.round(total / seconds)} fps, longest frame ${Math.round(worstAll)} ms, frames over 50 ms ${slowAll}`
}

async function run(): Promise<string[]> {
  const body = scroller()
  const out: string[] = []
  out.push(await phase("idle", 6000, 500, () => {}))
  // The way down and back is known before the phase: a read of the heights at every step would lay out the page itself.
  const bottom = body ? body.scrollHeight - body.clientHeight : 0
  let at = 0, direction = 1
  out.push(await phase("scroll", 8000, 16, () => {
    if (!body) return
    at = Math.min(bottom, Math.max(0, at + direction * 40))
    if (at === bottom || at === 0) direction = -direction
    body.scrollTop = at
  }))
  body?.scrollTo({ top: 0 })
  await pause(500)
  out.push(await phase("select", 6000, 150, (at) => {
    const all = cards()
    if (all.length) click(all[(at * 7) % all.length], { shiftKey: at % 3 === 2 })
  }))
  out.push(await phase("switch", 6000, 300, (at) => {
    const all = cards()
    const power = all[(at * 5) % Math.max(all.length, 1)]?.querySelector(".card-power button, .card-power input, .card-power [role=switch]")
    if (power) click(power)
  }))
  out.push(await phase("open", 8000, 700, (at) => {
    const all = cards()
    const card = all[(at * 3) % Math.max(all.length, 1)]
    card?.dispatchEvent(new MouseEvent("dblclick", { bubbles: true, cancelable: true, detail: 2 }))
  }))
  sim.phase = ""
  sim.phases = out
  return out
}

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
    <ModalShell :title="t('outputSim.title', { n: EPISODES })" size="full" fixed-height>
      <StatusOutput :series-id="SIM_SERIES" :series-name="t('outputSim.series')" />
    </ModalShell>
    <div class="sim-hud">{{ t("loadSim.hud", { fps: sim.fps, worst: sim.worst, apply: sim.apply }) }} {{ sim.phase }}</div>
  </div>
</template>

<style scoped>
.sim-page { padding: 1rem; }
.sim-hud {
  position: fixed; inset-block-start: 0.25rem; inset-inline-start: 0.25rem; z-index: 2000; padding: 0.25rem 0.5rem;
  background: #000; color: #0f0; font: 12px monospace; border-radius: 4px;
}
</style>
