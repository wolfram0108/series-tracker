import { defineStore } from "pinia"
import { ref } from "vue"
import { api } from "../api/client"
import type { components } from "../api/schema"

type SeriesSource = components["schemas"]["SeriesSource"]

/** A series. The contract of the core is extra="allow": fields beyond the declared ones may come (tmdb_info, …), hence
 *  the index signature. */
export interface Series {
  id: number
  name: string
  site: string
  auto_scan_enabled: boolean
  statuses: string[]
  is_busy?: boolean
  last_scan_time?: string | null
  season?: string | null
  sources?: components["schemas"]["SeriesSource"][]
  [key: string]: unknown
}

/** Дельта series_updated: всегда несёт id; statuses/is_busy обязательны
 *  (Р-18, находка 38). Прочие применённые поля — опционально. */
export type SeriesDelta = { id: number } & Partial<Series>

export const useSeriesStore = defineStore("series", () => {
  const list = ref<Series[]>([])
  // The list is read from the core: until it has been, the screen says it is being read, and where the read failed it says that, not that there are no series.
  const loaded = ref(false)
  const failed = ref(false)
  // id серий с оптимистичным «сохранением» — спиннер до falsy is_busy от SSE
  const savingIds = ref<Set<number>>(new Set())

  function byId(id: number): Series | undefined {
    return list.value.find((s) => s.id === id)
  }

  /** Полная загрузка списка (GET /api/series). */
  async function load(): Promise<void> {
    try {
      const { data } = await api.GET("/api/series")
      if (data) {
        list.value = data as unknown as Series[]
        loaded.value = true
      }
      failed.value = !data
    } catch {
      failed.value = true // the server is away: the window says so (ConnectionBanner), the list is read again when the stream is back
    }
  }

  /** Частичный merge дельты (series_updated): Object.assign только присланных
   *  полей в существующую серию — НЕ замена объекта. По falsy is_busy снимаем
   *  флаг сохранения (гонка оптимистичного UI и SSE, находка 38). */
  function applyDelta(delta: SeriesDelta): void {
    const s = byId(delta.id)
    if (!s) return
    Object.assign(s, delta)
    if ("is_busy" in delta && !delta.is_busy) savingIds.value.delete(delta.id)
  }

  /** A source of a series added, changed or taken out (series_sources_changed): the packet carries the source as it is
   *  stored now, none for one that went; the list stays in the order of the series. */
  function applySource(packet: { series_id: number; source_id: number; source?: SeriesSource | null }): void {
    const s = byId(packet.series_id)
    if (!s) return
    const others = (s.sources ?? []).filter((each) => each.id !== packet.source_id)
    s.sources = packet.source
      ? [...others, packet.source].sort((a, b) => (a.position ?? 0) - (b.position ?? 0) || a.id - b.id) : others
  }

  /** series_added: полный объект (вставка или замена по id). */
  function upsert(s: Series): void {
    const i = list.value.findIndex((x) => x.id === s.id)
    if (i >= 0) list.value[i] = s
    else list.value.push(s)
  }

  /** series_deleted. */
  function remove(id: number): void {
    list.value = list.value.filter((s) => s.id !== id)
    savingIds.value.delete(id)
  }

  /** Пометить серию «сохраняется» (оптимистично, до подтверждения по SSE). */
  function markSaving(id: number): void {
    savingIds.value.add(id)
  }

  function isSaving(id: number): boolean {
    return savingIds.value.has(id)
  }

  return { list, loaded, failed, savingIds, byId, load, applyDelta, applySource, upsert, remove, markSaving, isSaving }
})
