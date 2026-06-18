import { defineStore } from "pinia"
import { ref } from "vue"

/** Задача очереди — динамический объект контракта (DynamicObject), поля
 *  зависят от конкретной очереди. */
export type QueueTask = Record<string, unknown>

/** Очереди агентов — голые массивы (старый контракт сохранён). Наполняются
 *  push-событиями SSE; HTTP-загрузка — через настройки/агентов при открытии. */
export const useQueuesStore = defineStore("queues", () => {
  const agent = ref<QueueTask[]>([]) // agent_queue_update (у задач есть hash)
  const torrents = ref<QueueTask[]>([]) // torrent_progress_update (мониторинг)
  const downloads = ref<QueueTask[]>([]) // download_queue_update (yt-dlp)
  const slicing = ref<QueueTask[]>([]) // slicing_queue_update (ffmpeg)

  const setAgent = (t: QueueTask[]) => (agent.value = t)
  const setTorrents = (t: QueueTask[]) => (torrents.value = t)
  const setDownloads = (t: QueueTask[]) => (downloads.value = t)
  const setSlicing = (t: QueueTask[]) => (slicing.value = t)

  return { agent, torrents, downloads, slicing, setAgent, setTorrents, setDownloads, setSlicing }
})
