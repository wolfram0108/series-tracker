// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"

const client = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() }))
vi.mock("../../api/client", () => ({ api: client }))
vi.mock("primevue/usetoast", () => ({ useToast: () => ({ add: vi.fn() }) }))

import PrimeVue from "primevue/config"
import ExtensionChines from "./ExtensionChines.vue"
import { useProviderStore } from "../../stores/provider"

const LIMITS = { threads_total: 32, threads_per_file: 16, single_at_once: 4, provider_at_once: 2, multi_at_once: 2 }
const SITES = [{ name: "chines", label: "Чинес", sources: 10, lines: 7 }, { name: "second", label: "Второй", sources: 2, lines: 2 }]
const view = (extra: object = {}) => ({
  url: "http://127.0.0.1:5100", has_token: true, state: "up", reason: null, provider: "chinese_sources", version: "2.0.0", protocol: 2,
  revision: "3f9a0c7b21d4e58a", sites: SITES, downloads_dir: "/nas/work/downloads", seq: 1, ...extra,
})
const NONE = { url: "", has_token: false, state: "none", reason: null, provider: null, version: null, protocol: null, revision: null,
  sites: [], downloads_dir: "", seq: 1 }

let mounted: VueWrapper | null = null

function serve(first: object) {
  client.GET.mockImplementation(async (path: string) => ({ data: path === "/api/provider" ? first : LIMITS }))
}

describe("the tab of the provider of external sources", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    // A call a test does not answer gets an empty answer, as a core that says nothing back, not a missing one.
    Object.values(client).forEach((fn) => fn.mockReset().mockResolvedValue({}))
  })
  afterEach(() => mounted?.unmount())

  it("is the line of the address and nothing else while no address is named", async () => {
    serve(NONE)
    mounted = mount(ExtensionChines, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    expect(mounted.findAll("input")).toHaveLength(1)
    expect(mounted.find(".tile-grid").exists()).toBe(false)
    expect(mounted.text()).not.toContain("Папка загрузок")
  })

  it("shows the state of the socket, what the manifest says, the downloads and the sites once the address is named", async () => {
    serve(view())
    mounted = mount(ExtensionChines, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    const text = mounted.text()
    expect(text).toContain("Чинес работает")
    expect(text).toContain("chinese_sources 2.0.0")
    expect(text).toContain("протокол 2")
    expect(text).toContain("ревизия 3f9a0c7b21d4e58a")
    expect(text).toContain("/nas/work/downloads")
    expect(mounted.findAll(".tile").map((each) => each.text())).toEqual(["Чинес10 источников · 7 линий", "Второй2 источника · 2 линии"])
    expect((mounted.find("input[inputmode='numeric']").element as HTMLInputElement).value).toBe("2")
  })

  it("tells why the provider is not up and keeps the sites of its last manifest, dimmed", async () => {
    serve(view({ state: "down", reason: JSON.stringify({ code: "provider_unavailable" }) }))
    mounted = mount(ExtensionChines, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    expect(mounted.find(".pill-danger").text()).toBe("Чинес недоступен: внешний поставщик источников не отвечает")
    expect(mounted.find(".tile-grid").classes()).toContain("is-stale")
    expect(mounted.findAll(".tile")).toHaveLength(2)
  })

  it("writes the address when the field is left, with no token unless one is typed, and shows what the core holds", async () => {
    serve(NONE)
    mounted = mount(ExtensionChines, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    client.PUT.mockResolvedValueOnce({ data: { saved: true, problems: [], provider: view({ has_token: false, seq: 2 }) } })
    const address = mounted.find("input")
    await address.setValue("http://127.0.0.1:5100")
    await address.trigger("change")
    await flushPromises()
    expect(client.PUT).toHaveBeenCalledWith("/api/provider", { body: { url: "http://127.0.0.1:5100" } })
    expect(mounted.text()).toContain("Чинес работает")
  })

  it("sends a typed token once and forgets it, and takes it away by its own button", async () => {
    serve(view({ has_token: false }))
    mounted = mount(ExtensionChines, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    client.PUT.mockResolvedValueOnce({ data: { saved: true, problems: [], provider: view({ has_token: true, seq: 2 }) } })
    const secret = mounted.findAll("input")[1]
    await secret.setValue("s3cret")
    await secret.trigger("change")
    await flushPromises()
    expect(client.PUT).toHaveBeenCalledWith("/api/provider", { body: { url: "http://127.0.0.1:5100", token: "s3cret" } })
    expect((mounted.findAll("input")[1].element as HTMLInputElement).value).toBe("")
    client.PUT.mockResolvedValueOnce({ data: { saved: true, problems: [], provider: view({ has_token: false, seq: 3 }) } })
    await mounted.find(".btn-delete").trigger("click")
    await flushPromises()
    expect(client.PUT).toHaveBeenLastCalledWith("/api/provider", { body: { url: "http://127.0.0.1:5100", token: "" } })
  })

  it("shows why an address was refused and writes nothing of it into the picture", async () => {
    serve(NONE)
    mounted = mount(ExtensionChines, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    client.PUT.mockResolvedValueOnce({ data: { saved: false, provider: NONE, problems: [
      { field: "url", detail: { code: "provider_url_invalid", params: { url: "nope" } } }] } })
    const address = mounted.find("input")
    await address.setValue("nope")
    await address.trigger("change")
    await flushPromises()
    expect(mounted.find(".sheet-problem").text()).toContain("nope")
    expect(useProviderStore().view?.url).toBe("")
  })

  it("takes the limit of the downloads that go at once from the shared limits and writes only that limit", async () => {
    serve(view())
    mounted = mount(ExtensionChines, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    client.POST.mockResolvedValueOnce({ data: { ...LIMITS, provider_at_once: 3 } })
    const field = mounted.find("input[inputmode='numeric']")
    await field.setValue("3")
    await field.trigger("change")
    await flushPromises()
    expect(client.POST).toHaveBeenCalledWith("/api/settings/download_limits", { body: { provider_at_once: "3" } })
  })

  it("does not take a picture older than the one it shows", async () => {
    serve(view({ seq: 5 }))
    mounted = mount(ExtensionChines, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    useProviderStore().apply(view({ seq: 4, sites: [] }) as never)
    await flushPromises()
    expect(mounted.findAll(".tile")).toHaveLength(2)
    useProviderStore().apply(view({ seq: 6, sites: [SITES[0]] }) as never)
    await flushPromises()
    expect(mounted.findAll(".tile")).toHaveLength(1)
  })

  it("keeps a token typed while the address is still being saved: the answer to that save takes nothing but what it carried", async () => {
    serve(NONE)
    mounted = mount(ExtensionChines, { global: { plugins: [PrimeVue] } })
    await flushPromises()
    let answer: (value: unknown) => void = () => {}
    client.PUT.mockReturnValueOnce(new Promise((resolve) => { answer = resolve }))
    const address = mounted.find("input")
    await address.setValue("http://127.0.0.1:5100")
    await address.trigger("change")                       // the address is on its way
    const secret = mounted.findAll("input")[1]
    await secret.setValue("s3cret")                       // typed meanwhile
    answer({ data: { saved: true, problems: [], provider: view({ has_token: false, seq: 2 }) } })
    await flushPromises()
    expect((mounted.findAll("input")[1].element as HTMLInputElement).value).toBe("s3cret")
    client.PUT.mockResolvedValueOnce({ data: { saved: true, problems: [], provider: view({ has_token: true, seq: 3 }) } })
    await mounted.findAll("input")[1].trigger("change")
    await flushPromises()
    expect(client.PUT).toHaveBeenLastCalledWith("/api/provider", { body: { url: "http://127.0.0.1:5100", token: "s3cret" } })
    expect((mounted.findAll("input")[1].element as HTMLInputElement).value).toBe("")
  })
})
