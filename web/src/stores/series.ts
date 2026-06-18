import { defineStore } from "pinia"
import { ref } from "vue"
import { api } from "../api/client"

/** Серия. Контракт бэка — extra="allow", поэтому помимо явных полей могут
 *  приходить и другие (tmdb_info, tracker_info, …) — отсюда index-signature. */
export interface Series {
  id: number
  name: string
  site: string
  auto_scan_enabled: boolean
  statuses: string[]
  is_busy?: boolean
  last_scan_time?: string | null
  season?: string | null
  [key: string]: unknown
}

/** Дельта series_updated: всегда несёт id; statuses/is_busy обязательны
 *  (Р-18, находка 38). Прочие применённые поля — опционально. */
export type SeriesDelta = { id: number } & Partial<Series>

export const useSeriesStore = defineStore("series", () => {
  const list = ref<Series[]>([])
  // id серий с оптимистичным «сохранением» — спиннер до falsy is_busy от SSE
  const savingIds = ref<Set<number>>(new Set())

  function byId(id: number): Series | undefined {
    return list.value.find((s) => s.id === id)
  }

  /** Полная загрузка списка (GET /api/series). */
  async function load(): Promise<void> {
    const { data } = await api.GET("/api/series")
    if (data) list.value = data as unknown as Series[]
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

  return { list, savingIds, byId, load, applyDelta, upsert, remove, markSaving, isSaving }
})
