import { ref } from "vue"
import { useToast } from "primevue/usetoast"

/** Тело ошибки бэка по контракту (schemas.py: ErrorResponse/ErrorOnly). */
function extractError(error: unknown): string | null {
  if (error && typeof error === "object" && "error" in error) {
    return String((error as { error: unknown }).error)
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
        const detail = extractError(error) || opts.errorMessage || "Ошибка запроса"
        toast.add({ severity: "error", summary: "Ошибка", detail, life: 5000 })
        return null
      }
      return (data ?? null) as T | null
    } catch (e) {
      toast.add({
        severity: "error",
        summary: "Ошибка сети",
        detail: opts.errorMessage || String(e),
        life: 5000,
      })
      return null
    } finally {
      loading.value = false
    }
  }

  return { request, loading }
}
