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
  id: string; label: string; key: string | null; series_id: number | null; status: string; progress: number; stage: string; stage_pct: number
  detail: string; dub_index: number; dub_total: number; cur_dub: string; dubs: string[]; elapsed_s: number
  created_at: string | null; error: string | null; results: ConformResult[]
}
export interface ConformPicture {
  daemon: { up: boolean; error?: string | null; version?: string; device?: string; gpu_name?: string }
  tasks: ConformTask[]
}

/** A container a sync builds (pack_queue_update, core/contracts/packing.py). */
export type PackCard = components["schemas"]["PackCard"]
/** The journal of a series being carried out (pack_journal_update, core/contracts/packing.py). */
export type PackJournal = components["schemas"]["PackJournal"]

/** Очереди агентов — голые массивы (старый контракт сохранён). Наполняются
 *  push-событиями SSE; HTTP-загрузка — через настройки/агентов при открытии. */
// Each queue is a picture the core pushes whole and the window only reads, so it is held without a deep proxy.
export const useQueuesStore = defineStore("queues", () => {
  const agent = shallowRef<QueueTask[]>([]) // agent_queue_update (its tasks have a hash)
  const torrents = shallowRef<QueueTask[]>([]) // torrent_progress_update (the monitor)
  const downloads = shallowRef<DownloadCard[]>([]) // download_queue_update: the cards of the downloads (core/contracts/downloads.py)
  const conform = shallowRef<ConformPicture | null>(null) // conform_queue_update (alignment daemon)
  const pack = shallowRef<PackCard[]>([]) // pack_queue_update (the containers of the episodes)
  const journals = shallowRef<PackJournal[]>([]) // pack_journal_update (the journals being carried out)

  const setAgent = (t: QueueTask[]) => (agent.value = t)
  const setTorrents = (t: QueueTask[]) => (torrents.value = t)
  const setDownloads = (t: DownloadCard[]) => (downloads.value = t)
  const setConform = (p: ConformPicture) => (conform.value = p)
  const setPack = (t: PackCard[]) => (pack.value = t)
  const setJournals = (t: PackJournal[]) => (journals.value = t)

  return { agent, torrents, downloads, conform, pack, journals, setAgent, setTorrents, setDownloads, setConform, setPack,
    setJournals }
})
