import { defineStore } from "pinia"
import { ref } from "vue"

/** Задача очереди — динамический объект контракта (DynamicObject), поля
 *  зависят от конкретной очереди. */
export type QueueTask = Record<string, unknown>

/** Alignment daemon picture pushed with every change (conform_queue_update). */
export interface ConformResult {
  dub: string; ok: boolean; skipped: boolean; resid_ms: number | null; coverage: number | null
  cuts: number | null; suspect: boolean; warnings: string[]; critical: string[]; error: string | null
}
export interface ConformTask {
  id: string; label: string; status: string; progress: number; stage: string; stage_pct: number
  detail: string; dub_index: number; dub_total: number; cur_dub: string; elapsed_s: number
  error: string | null; results: ConformResult[]
}
export interface ConformPicture {
  daemon: { up: boolean; error?: string | null; version?: string; device?: string; gpu_name?: string }
  tasks: ConformTask[]
}

/** Очереди агентов — голые массивы (старый контракт сохранён). Наполняются
 *  push-событиями SSE; HTTP-загрузка — через настройки/агентов при открытии. */
export const useQueuesStore = defineStore("queues", () => {
  const agent = ref<QueueTask[]>([]) // agent_queue_update (у задач есть hash)
  const torrents = ref<QueueTask[]>([]) // torrent_progress_update (мониторинг)
  const downloads = ref<QueueTask[]>([]) // download_queue_update (yt-dlp)
  const slicing = ref<QueueTask[]>([]) // slicing_queue_update (ffmpeg)
  const conform = ref<ConformPicture | null>(null) // conform_queue_update (alignment daemon)

  const setAgent = (t: QueueTask[]) => (agent.value = t)
  const setTorrents = (t: QueueTask[]) => (torrents.value = t)
  const setDownloads = (t: QueueTask[]) => (downloads.value = t)
  const setSlicing = (t: QueueTask[]) => (slicing.value = t)
  const setConform = (p: ConformPicture) => (conform.value = p)

  return { agent, torrents, downloads, slicing, conform, setAgent, setTorrents, setDownloads, setSlicing, setConform }
})
