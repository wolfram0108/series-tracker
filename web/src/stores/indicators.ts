import { defineStore } from "pinia"
import { computed } from "vue"
import { useQueuesStore } from "./queues"
import { useScannerStore } from "./scanner"

/** The lamps of the agents in the header, derived from the queues and the scanner: they light and go out at once (the hold
 *  timer of the old app.js was never called, so it was not carried over). */
export const useIndicatorsStore = defineStore("indicators", () => {
  const queues = useQueuesStore()
  const scanner = useScannerStore()

  const monitoring = computed<boolean>(() => {
    const s = scanner.status
    return s ? s.is_scanning || s.is_awaiting_tasks : false
  })
  // A card of a download that ended stays for a while after its sync: only one that waits or goes is work.
  const downloader = computed<boolean>(() => queues.downloads.some((card) => card.status !== "done" && card.status !== "failed"))

  return { monitoring, downloader }
})
