import type { Series } from "../stores/series"
import type { ConformTask, DownloadCard, PackCard } from "../stores/queues"

// The sample load of tools/LoadSim.vue: series and works of a sync, every figure of which moves with the beat `tick`.

const STATES = ["downloading", "aligning", "packing", "scanning"]

export const simSeries = (count: number): Series[] => Array.from({ length: count }, (_, at): Series => ({
  id: at + 1, name: `Сериал ${at + 1}`, site: "example.org", auto_scan_enabled: at % 2 === 0,
  statuses: [STATES[at % STATES.length], "waiting"], last_scan_time: null,
}))

const variant = (at: number) => ({ source_id: at % 7, source_n: (at % 3) + 1, source_color: at % 5, place: "линия", player: "line:x", player_label: "CO4K", height: 2160 })
const episode = (at: number) => `s01e${String((at % 700) + 1).padStart(3, "0")}`

export function simDownloads(count: number, series: number, tick: number): DownloadCard[] {
  return Array.from({ length: count }, (_, at) => {
    const progress = (tick * 3 + at) % 100
    const id = (at % series) + 1
    return {
      id: `d${at}`, need: { key: `d${at}`, series_id: id, series_name: `Сериал ${id}`, sync_started: "2026-11-21T10:00:00Z",
        episode: episode(at), kind: at % 3 ? "dub" : "video", group: at % 3 ? `Озвучка ${at % 9}` : null, voice_number: at % 3 ? (at % 9) + 1 : null, cap: 2160 },
      variant: variant(at), status: "download", method: "hls",
      stages: [{ name: "queue", state: "done", seconds: 3 }, { name: "link", state: "done", seconds: 1 },
        { name: "download", state: "active", seconds: tick }, { name: "remux", state: "pending", seconds: 0 }, { name: "check", state: "pending", seconds: 0 }],
      log: [{ kind: "attempt", variant: variant(at), attempt: 1, of: 3, outcome: "running", stage: "download", seconds: tick }],
      figures: { progress, done_mb: progress * 4, total_mb: 400, speed: 10 + (tick % 7), eta: 100 - progress, source_seconds: 1440 },
      replanned: false, position: null, running: 8, limit: 8, queued_at: "2026-11-21T10:00:00Z", finished_at: "", seconds: tick,
    } as DownloadCard
  })
}

export function simConform(count: number, series: number, tick: number): ConformTask[] {
  return Array.from({ length: count }, (_, at) => ({
    id: `c${at}`, label: `Сериал ${(at % series) + 1} ${episode(at)}`, key: null, series_id: (at % series) + 1, status: "running",
    progress: (tick * 2 + at) % 100, stage: "align", stage_pct: (tick * 5 + at) % 100, detail: "", dub_index: 1, dub_total: 2,
    cur_dub: "b.mka", dubs: ["a.mka", "b.mka"], elapsed_s: tick, created_at: "2026-11-21T10:00:00Z", error: null, results: [],
  }))
}

export function simPack(count: number, series: number, tick: number): PackCard[] {
  return Array.from({ length: count }, (_, at) => {
    const id = (at % series) + 1
    return {
      id: `p${at}`, series_id: id, series_name: `Сериал ${id}`, sync_started: "2026-11-21T10:00:00Z", episode: episode(at),
      name: `Сериал ${id} ${episode(at)}.mkv`, video: variant(at), status: "build",
      stages: [{ name: "queue", state: "done", seconds: 2 }, { name: "build", state: "active", seconds: tick }, { name: "verify", state: "pending", seconds: 0 },
        { name: "apply", state: "pending", seconds: 0 }],
      tracks: [{ number: 1, label: "Озвучка 1", kind: "new" }, { number: 2, label: "Озвучка 2", kind: "kept" }],
      reason: "", waited: false, failure: "", figures: { progress: (tick * 4 + at) % 100, written_mb: tick, speed_x: 12, eta: 30 },
      queued_at: "2026-11-21T10:00:00Z", finished_at: "", seconds: tick,
    } as PackCard
  })
}
