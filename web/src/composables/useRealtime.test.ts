import { beforeEach, describe, expect, it, vi } from "vitest"
import { nextTick, ref } from "vue"
import { createPinia, setActivePinia } from "pinia"

const connected = ref(false)
vi.mock("./useSSE", () => ({
  useSSE: () => ({ connected, on: () => () => undefined, connect: () => undefined, disconnect: () => undefined }),
}))

import { setupRealtime } from "./useRealtime"
import { useSeriesStore } from "../stores/series"
import { useScannerStore } from "../stores/scanner"

describe("a connection that comes back", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    connected.value = false
  })

  it("reads the list of series and the scanner again; the first connection leaves them to the start", async () => {
    const series = vi.spyOn(useSeriesStore(), "load").mockResolvedValue()
    const scanner = vi.spyOn(useScannerStore(), "load").mockResolvedValue()
    setupRealtime()

    connected.value = true
    await nextTick()
    expect(series).not.toHaveBeenCalled()
    expect(scanner).not.toHaveBeenCalled()

    connected.value = false // the service restarted: what it said meanwhile never came
    await nextTick()
    connected.value = true
    await nextTick()
    expect(series).toHaveBeenCalledTimes(1)
    expect(scanner).toHaveBeenCalledTimes(1)
  })
})
