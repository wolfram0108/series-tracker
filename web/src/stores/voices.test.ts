import { beforeEach, describe, expect, it } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { useVoicesStore, type VoiceSettingsView } from "./voices"

describe("voices store", () => {
  beforeEach(() => setActivePinia(createPinia()))

  it("keeps the newest settings the core announced", () => {
    const store = useVoicesStore()
    const view = (first: string): VoiceSettingsView => ({
      priority: ["kodik"], voice_groups: { [first]: [] }, default_priority: ["kodik"], rejected: [],
    })
    expect(store.settings).toBeNull()
    store.applySettings(view("first"))
    store.applySettings(view("second"))
    expect(Object.keys(store.settings?.voice_groups ?? {})).toEqual(["second"])
  })

  it("names a file of the alignment queue by its name without the path and the last extension", () => {
    const store = useVoicesStore()
    expect(store.dubName("cf1", "blackcat.mp4")).toBe("blackcat")
    expect(store.dubName("cf1", "/mnt/dl/Show/blackcat.mkv")).toBe("blackcat")
    expect(store.dubName("cf2", "archive.tar.gz")).toBe("archive.tar")
    expect(store.dubName("cf2", "no-extension")).toBe("no-extension")
  })
})
