import { ref } from "vue"
import { useToast } from "primevue/usetoast"
import { api } from "../api/client"
import { t } from "../i18n"
import { useApi } from "./useApi"

/** The limits of the downloads by the way they go (downloads, limits.py; GET and POST /api/settings/download_limits). The core takes
 *  them whole and answers with what applies; the windows that show a part of them (the pipeline of the debug tab, the provider of
 *  the extensions) share this one state, so that a save from one never takes back what the other shows. */
export interface DownloadLimits {
  threads_total: number
  threads_per_file: number
  single_at_once: number
  provider_at_once: number
  multi_at_once: number
}

type Change = Partial<Record<"threads_total" | "threads_per_file" | "single_at_once" | "provider_at_once", string>>

const limits = ref<DownloadLimits | null>(null)

export function useDownloadLimits() {
  const { request } = useApi()
  const toast = useToast()

  async function load(): Promise<void> {
    const got = (await request(api.GET("/api/settings/download_limits"))) as DownloadLimits | null
    if (got) limits.value = got
  }

  /** Writes the limits named in `change` and nothing else: the core holds the rest and merges, so a window never sends a limit it
   *  has not been shown (not loaded yet, or changed by another window meanwhile). What the core refuses is read back. */
  async function save(change: Change): Promise<void> {
    const saved = (await request(api.POST("/api/settings/download_limits", { body: change }))) as DownloadLimits | null
    if (saved !== null) {
      limits.value = saved
      toast.add({ severity: "info", summary: t("debug.downloads.saved"), life: 2000 })
    } else {
      await load()
    }
  }

  return { limits, load, save }
}
