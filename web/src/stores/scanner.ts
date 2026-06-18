import { defineStore } from "pinia"
import { ref } from "vue"
import { api } from "../api/client"

/** Статус сканера (контракт ScannerStatus). next_scan_time — ISO или null. */
export interface ScannerStatus {
  scanner_enabled: boolean
  is_scanning: boolean
  is_awaiting_tasks: boolean
  scan_interval: number
  next_scan_time: string | null
}

export const useScannerStore = defineStore("scanner", () => {
  const status = ref<ScannerStatus | null>(null)

  /** scanner_status_update (SSE) — полная замена статуса. */
  function set(s: ScannerStatus): void {
    status.value = s
  }

  /** Стартовая загрузка (GET /api/scanner/status). */
  async function load(): Promise<void> {
    const { data } = await api.GET("/api/scanner/status")
    if (data) status.value = data as unknown as ScannerStatus
  }

  return { status, set, load }
})
