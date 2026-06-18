import { useSSE } from "./useSSE"
import { useSeriesStore, type Series, type SeriesDelta } from "../stores/series"
import { useQueuesStore, type QueueTask } from "../stores/queues"
import { useScannerStore, type ScannerStatus } from "../stores/scanner"

/** Связывает SSE-события со сторами и открывает соединение. Вызывается один
 *  раз при старте приложения (после установки Pinia). Маппинг 1:1 с SSE_MAP
 *  бэка; renaming_complete — для статус-модалки (подключится в Ф4). */
export function setupRealtime(): void {
  const sse = useSSE()
  const series = useSeriesStore()
  const queues = useQueuesStore()
  const scanner = useScannerStore()

  sse.on("series_updated", (d) => series.applyDelta(d as SeriesDelta))
  sse.on("series_added", (d) => series.upsert(d as Series))
  sse.on("series_deleted", (d) => series.remove((d as { id: number }).id))

  sse.on("agent_queue_update", (d) => queues.setAgent(d as QueueTask[]))
  sse.on("torrent_progress_update", (d) => queues.setTorrents(d as QueueTask[]))
  sse.on("download_queue_update", (d) => queues.setDownloads(d as QueueTask[]))
  sse.on("slicing_queue_update", (d) => queues.setSlicing(d as QueueTask[]))

  sse.on("scanner_status_update", (d) => scanner.set(d as ScannerStatus))

  // relocation: бэк помечает серию занятой на старте и шлёт обновление в конце
  sse.on("relocation_started", (d) =>
    series.applyDelta({ id: (d as { series_id: number }).series_id, is_busy: true }),
  )
  sse.on("relocation_finished", () => {
    void series.load()
  })
  // renaming_complete — обновление состава в статус-модалке (Ф4)

  sse.connect()
}
