import { defineStore } from "pinia"
import { ref } from "vue"

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
export const useQueuesStore = defineStore("queues", () => {
  const agent = ref<QueueTask[]>([]) // agent_queue_update (у задач есть hash)
  const torrents = ref<QueueTask[]>([]) // torrent_progress_update (мониторинг)
  const downloads = ref<QueueTask[]>([]) // download_queue_update (yt-dlp)
  const slicing = ref<QueueTask[]>([]) // slicing_queue_update (ffmpeg)
  const conform = ref<ConformPicture | null>(null) // conform_queue_update (alignment daemon)
  const pack = ref<PackTask[]>([]) // pack_queue_update (the containers of the episodes)

  const setAgent = (t: QueueTask[]) => (agent.value = t)
  const setTorrents = (t: QueueTask[]) => (torrents.value = t)
  const setDownloads = (t: QueueTask[]) => (downloads.value = t)
  const setSlicing = (t: QueueTask[]) => (slicing.value = t)
  const setConform = (p: ConformPicture) => (conform.value = p)
  const setPack = (t: PackTask[]) => (pack.value = t)

  return { agent, torrents, downloads, slicing, conform, pack, setAgent, setTorrents, setDownloads, setSlicing, setConform, setPack }
})
