import { watch } from "vue"
import { useSSE } from "./useSSE"
import { useSeriesStore, type Series, type SeriesDelta } from "../stores/series"
import { useQueuesStore, type ConformPicture, type DownloadCard, type PackTask, type QueueTask } from "../stores/queues"
import { useScannerStore, type ScannerStatus } from "../stores/scanner"
import { useSourceTablesStore } from "../stores/sourceTables"
import { useSeriesReadsStore } from "../stores/seriesReads"
import { useCompositionStore } from "../stores/composition"
import type { components } from "../api/schema"
import { useConformStore, type ConformSettingsView } from "../stores/conform"
import { useVoicesStore, type VoiceSettingsView } from "../stores/voices"
import { useSourcesStore, type SourcesSettingsView } from "../stores/sources"
import { usePlanStore } from "../stores/plan"

type Schemas = components["schemas"]

/** Связывает SSE-события со сторами и открывает соединение. Вызывается один
 *  раз при старте приложения (после установки Pinia). Маппинг 1:1 с SSE_MAP
 *  бэка; renaming_complete — для статус-модалки (подключится в Ф4). */
export function setupRealtime(): void {
  const sse = useSSE()
  const series = useSeriesStore()
  const queues = useQueuesStore()
  const scanner = useScannerStore()
  const tables = useSourceTablesStore()
  const reads = useSeriesReadsStore()
  const composition = useCompositionStore()
  const voices = useVoicesStore()
  const sources = useSourcesStore()
  const plan = usePlanStore()
  const conform = useConformStore()

  sse.on("series_updated", (d) => series.applyDelta(d as SeriesDelta))
  sse.on("series_added", (d) => series.upsert(d as Series))
  sse.on("series_deleted", (d) => series.remove((d as { id: number }).id))

  sse.on("agent_queue_update", (d) => queues.setAgent(d as QueueTask[]))
  sse.on("torrent_progress_update", (d) => queues.setTorrents(d as QueueTask[]))
  sse.on("download_queue_update", (d) => queues.setDownloads(d as DownloadCard[]))
  sse.on("slicing_queue_update", (d) => queues.setSlicing(d as QueueTask[]))
  sse.on("conform_queue_update", (d) => queues.setConform(d as ConformPicture))
  sse.on("pack_queue_update", (d) => queues.setPack(d as PackTask[]))
  sse.on("plan_update", (d) => plan.apply(d as Schemas["PlannerView"]))
  sse.on("voice_settings_update", (d) => voices.applySettings(d as VoiceSettingsView))
  sse.on("sources_settings_update", (d) => sources.applySettings(d as SourcesSettingsView))
  sse.on("conform_settings_update", (d) => conform.applySettings(d as ConformSettingsView))

  sse.on("scanner_status_update", (d) => scanner.set(d as ScannerStatus))

  // Reading of site pages and the layout: packets are whole for their part of the screen, nothing is asked back.
  sse.on("sources_table_progress", (d) => tables.applyProgress(d as Schemas["TableProgress"]))
  sse.on("series_scan_progress", (d) => reads.apply(d as Schemas["SeriesReadProgress"]))
  sse.on("sources_table_ready", (d) => tables.applyReady(d as Schemas["TableReady"]))
  sse.on("sources_table_failed", (d) => tables.applyFailed(d as Schemas["TableFailed"]))
  sse.on("composition_changed", (d) => composition.applyChanged(d as Schemas["CompositionChanged"]))
  sse.on("composition_failed", (d) => composition.applyFailed(d as Schemas["CompositionFailed"]))
  sse.on("series_sources_changed", (d) => {
    composition.applySourcesChanged(d as Schemas["SourceChanged"])
    series.applySource(d as Schemas["SourceChanged"])
  })

  // relocation: бэк помечает серию занятой на старте и шлёт обновление в конце
  sse.on("relocation_started", (d) =>
    series.applyDelta({ id: (d as { series_id: number }).series_id, is_busy: true }),
  )
  sse.on("relocation_finished", () => {
    void series.load()
  })
  // renaming_complete — обновление состава в статус-модалке (Ф4)

  // A connection that came back may have missed events (the end of a read, of a sync, a restart of the service): the
  // soft news of the pages are dropped, the list of series and the scanner are read again as at the start, and every
  // open screen reads its API again (useSeriesSources / useSourceDrafts / useSeriesOutput).
  let upBefore = false
  watch(sse.connected, (up) => {
    if (!up) return
    tables.reset()
    reads.reset()
    if (upBefore) {
      void series.load()
      void scanner.load()
    }
    upBefore = true
  })

  sse.connect()
}
