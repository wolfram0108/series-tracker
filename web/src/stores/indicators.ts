import { defineStore } from "pinia"
import { computed } from "vue"
import { useQueuesStore } from "./queues"
import { useScannerStore } from "./scanner"

/** Индикаторы агентов (monitoring/downloader/slicing) в шапке.
 *
 *  ЭМПИРИЧЕСКАЯ НАХОДКА (Ф3): в исходном app.js функция _updateIndicatorState
 *  с hold-таймером 1000ms (на неё ссылался §11 ТЗ) ОПРЕДЕЛЕНА, НО НЕ
 *  ВЫЗЫВАЕТСЯ — мёртвый код. Реальные индикаторы загораются/гаснут мгновенно
 *  как производные от очередей и статуса сканера. Переносим реальное
 *  поведение, а не гипотезу ТЗ. Если hold понадобится — добавится отдельно. */
export const useIndicatorsStore = defineStore("indicators", () => {
  const queues = useQueuesStore()
  const scanner = useScannerStore()

  const monitoring = computed<boolean>(() => {
    const s = scanner.status
    return s ? s.is_scanning || s.is_awaiting_tasks : false
  })
  const downloader = computed<boolean>(() => queues.downloads.length > 0)
  const slicing = computed<boolean>(() => queues.slicing.length > 0)

  return { monitoring, downloader, slicing }
})
