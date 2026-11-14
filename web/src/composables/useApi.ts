import { ref } from "vue"
import { useToast } from "primevue/usetoast"
import { reasonText } from "../lib/reason"
import { t } from "../i18n"

// An error stays long enough to be read through: it is what the user must act on.
const ERROR_LIFE_MS = 10_000

/** Тело ошибки бэка по контракту (schemas.py: ErrorResponse/ErrorOnly). */
function extractError(error: unknown): string | null {
  if (error && typeof error === "object" && "error" in error) {
    return reasonText(String((error as { error: unknown }).error))
  }
  return null
}

/** Обёртка над вызовами openapi-fetch: единый loading и перехват ошибок → Toast.
 *  `request(call)` принимает промис openapi-fetch ({ data, error }) и возвращает
 *  data, либо null при ошибке (показав тост). Сетевые сбои тоже ловятся. */
export function useApi() {
  const loading = ref(false)
  const toast = useToast()

  async function request<T>(
    call: Promise<{ data?: T; error?: unknown }>,
    opts: { errorMessage?: string } = {},
  ): Promise<T | null> {
    loading.value = true
    try {
      const { data, error } = await call
      if (error) {
        const detail = extractError(error) || opts.errorMessage || t("common.requestFailed")
        toast.add({ severity: "error", summary: t("common.error"), detail, life: ERROR_LIFE_MS })
        return null
      }
      return (data ?? null) as T | null
    } catch (e) {
      // The exception is for the log of the browser; the window says what it means for the user's action.
      console.error(e)
      toast.add({
        severity: "error",
        summary: t("common.networkFailed"),
        detail: opts.errorMessage || t("common.notDone"),
        life: ERROR_LIFE_MS,
      })
      return null
    } finally {
      loading.value = false
    }
  }

  return { request, loading }
}
