import { defineStore } from "pinia"
import { ref } from "vue"
import { api } from "../api/client"

/** UI-состояние: активная серия (открытая статус-модалка) и жизненный цикл
 *  `viewing`. viewing эфемерный на бэке (Р-11): открытие модалки ставит серии
 *  состояние ['viewing'], закрытие — []. Сброс при обрыве SSE делает catalog. */
export const useUiStore = defineStore("ui", () => {
  const activeSeriesId = ref<number | null>(null)

  async function setState(id: number, state: string[]): Promise<void> {
    await api.POST("/api/series/{series_id}/state", {
      params: { path: { series_id: id } },
      body: { state },
    } as never)
  }

  /** Открыть статус-модалку серии: пометить серию как просматриваемую. */
  async function openStatus(id: number): Promise<void> {
    activeSeriesId.value = id
    await setState(id, ["viewing"])
  }

  /** Закрыть статус-модалку: снять viewing и активную серию. */
  async function closeStatus(): Promise<void> {
    const id = activeSeriesId.value
    activeSeriesId.value = null
    if (id != null) await setState(id, [])
  }

  return { activeSeriesId, openStatus, closeStatus }
})
