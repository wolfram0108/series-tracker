import { defineStore } from "pinia"
import { ref } from "vue"
import { api } from "../api/client"
import type { components } from "../api/schema"

/** The state of the scheduler (contract ScannerStatus): the switch, the schedule of the day, the next sync and check. */
export type ScannerStatus = components["schemas"]["ScannerStatus"]

export const useScannerStore = defineStore("scanner", () => {
  const status = ref<ScannerStatus | null>(null)

  /** scanner_status_update (SSE): the whole status, as the core holds it now. */
  function set(s: ScannerStatus): void {
    status.value = s
  }

  /** The first read (GET /api/scanner/status). */
  async function load(): Promise<void> {
    const { data } = await api.GET("/api/scanner/status")
    if (data) status.value = data
  }

  return { status, set, load }
})
