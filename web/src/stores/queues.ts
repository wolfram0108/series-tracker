import { defineStore } from "pinia"
import { shallowRef } from "vue"
import type { components } from "../api/schema"

/** A download as the core keeps it: one need of a plan through every variant it took (core/contracts/downloads.py). */
export type DownloadCard = components["schemas"]["DownloadCard"]

/** Задача очереди — динамический объект контракта (DynamicObject), поля
 *  зависят от конкретной очереди. */
export type QueueTask = Record<string, unknown>

/** Alignment daemon picture pushed with every change (conform_queue_update). */
export interface ConformResult {
  dub: string; plot: string | null; ok: boolean; refused: boolean; skipped: boolean; resid_ms: number | null; coverage: number | null
  cuts: number | null; suspect: boolean; warnings: string[]; critical: string[]; error: string | null
}
export interface ConformTask {
  id: string; label: string; key: string | null; status: string; progress: number; stage: string; stage_pct: number
  detail: string; dub_index: number; dub_total: number; cur_dub: string; dubs: string[]; elapsed_s: number
  created_at: string | null; error: string | null; results: ConformResult[]
}
export interface ConformPicture {
  daemon: { up: boolean; error?: string | null; version?: string; device?: string; gpu_name?: string }
  tasks: ConformTask[]
}

/** A task of the packing of a container (pack_queue_update): a series waiting for its turn (no season), a slot, or a
 *  publication a stop cut short. */
export interface PackTask {
  id: string; series_id: number; series_name: string; season: number | null; episode: number | null; part: number | null
  name: string; status: "queued" | "building" | "packed" | "unchanged" | "waiting" | "failed" | "interrupted"; tracks: string[]
}

/** Очереди агентов — голые массивы (старый контракт сохранён). Наполняются
 *  push-событиями SSE; HTTP-загрузка — через настройки/агентов при открытии. */
// Each queue is a picture the core pushes whole and the window only reads, so it is held without a deep proxy.
export const useQueuesStore = defineStore("queues", () => {
  const agent = shallowRef<QueueTask[]>([]) // agent_queue_update (у задач есть hash)
  const torrents = shallowRef<QueueTask[]>([]) // torrent_progress_update (мониторинг)
  const downloads = shallowRef<DownloadCard[]>([]) // download_queue_update: the cards of the downloads (core/contracts/downloads.py)
  const slicing = shallowRef<QueueTask[]>([]) // slicing_queue_update (ffmpeg)
  const conform = shallowRef<ConformPicture | null>(null) // conform_queue_update (alignment daemon)
  const pack = shallowRef<PackTask[]>([]) // pack_queue_update (the containers of the episodes)

  const setAgent = (t: QueueTask[]) => (agent.value = t)
  const setTorrents = (t: QueueTask[]) => (torrents.value = t)
  const setDownloads = (t: DownloadCard[]) => (downloads.value = t)
  const setSlicing = (t: QueueTask[]) => (slicing.value = t)
  const setConform = (p: ConformPicture) => (conform.value = p)
  const setPack = (t: PackTask[]) => (pack.value = t)

  return { agent, torrents, downloads, slicing, conform, pack, setAgent, setTorrents, setDownloads, setSlicing, setConform, setPack }
})
