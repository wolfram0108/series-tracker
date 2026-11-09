// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"

const client = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() }))
vi.mock("../../api/client", () => ({ api: client }))
vi.mock("primevue/usetoast", () => ({ useToast: () => ({ add: vi.fn() }) }))

import SettingsConformServer from "./SettingsConformServer.vue"
import { useSSE } from "../../composables/useSSE"

const view = (url: string) => ({ url, work_dir: "/nas/work", work_dir_remote: "/mnt/work" })

let mounted: VueWrapper | null = null

describe("the settings of the alignment server", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    Object.values(client).forEach((fn) => fn.mockReset())
  })
  afterEach(() => mounted?.unmount())

  it("are read again when the connection comes back: a save made meanwhile was not announced to the screen", async () => {
    const connected = useSSE().connected
    connected.value = true
    client.GET.mockResolvedValueOnce({ data: view("http://old.test:8787") })
    mounted = mount(SettingsConformServer)
    await flushPromises()
    const shown = () => (mounted!.find("input").element as HTMLInputElement).value
    expect(shown()).toBe("http://old.test:8787")

    connected.value = false
    await flushPromises()
    client.GET.mockResolvedValueOnce({ data: view("http://new.test:8787") })
    connected.value = true
    await flushPromises()
    expect(client.GET).toHaveBeenCalledTimes(2)
    expect(shown()).toBe("http://new.test:8787")
    connected.value = false
  })
})
