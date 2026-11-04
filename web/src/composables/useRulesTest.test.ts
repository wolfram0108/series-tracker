// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { useRulesTest } from "./useRulesTest"
import * as calls from "./rulesTestCalls"
import * as address from "./sourceAddress"
import type { RulesTestReply } from "./rulesTestCalls"
import { useSourceTablesStore } from "../stores/sourceTables"
import { useSSE } from "./useSSE"
import { ru } from "../i18n/ru"

vi.mock("./rulesTestCalls")
vi.mock("./sourceAddress")

const PAGE = "https://1.ruchime.org/release/test"
const ok = <T>(data: T) => ({ ok: true as const, data })
const reply = (over: Partial<RulesTestReply> = {}): RulesTestReply => ({ url: PAGE, state: "ready", lines: [], ...over })
const line = (key: string, text: string, episode: number | null = null) => ({
  key, line: text, result: episode === null ? null : { excluded: false, season: 1, episode, events: [], errors: [] },
})
const site = (url = PAGE, sourceType: "web_video" | "vk_video" = "web_video") =>
  vi.mocked(address.typeAddress).mockResolvedValue({ kind: "site", sourceType, url })

beforeEach(() => {
  setActivePinia(createPinia())
  useSSE().connected.value = false
  vi.mocked(calls.testProfile).mockReset().mockResolvedValue(ok(reply()))
  vi.mocked(calls.fetchFilenames).mockReset()
  vi.mocked(address.typeAddress).mockReset()
})

describe("useRulesTest", () => {
  it("reads a page and shows every row with the answer of the profile, and keeps the lines for editing", async () => {
    site()
    vi.mocked(calls.testProfile).mockResolvedValue(ok(reply({ lines: [line("a", "Серия 1", 1), line("b", "Трейлер")] })))
    const test = useRulesTest(() => 5)
    await test.readPage(PAGE, "", "search")
    expect(calls.testProfile).toHaveBeenCalledWith({
      url: PAGE, options: {}, profile_id: 5, series_season: null, refresh: true,
    })
    expect(test.state.value).toBe("ready")
    expect(test.lines.value.map((each) => [each.key, each.result?.episode ?? null])).toEqual([["a", 1], ["b", null]])
    expect(test.fetched.value).toEqual(["Серия 1", "Трейлер"])
  })

  it("offers the rows of the cards in the field in every state: while the page is read and after a failed read too", async () => {
    site()
    const stale = [line("a", "Серия 1", 1), line("b", "Трейлер")]
    for (const state of ["reading", "failed"] as const) {
      vi.mocked(calls.testProfile).mockResolvedValue(ok(reply({ state, lines: stale })))
      const test = useRulesTest(() => 5)
      await test.readPage(PAGE, "", "search")
      expect(test.state.value).toBe(state)
      expect(test.lines.value).toHaveLength(2)
      expect(test.fetched.value).toEqual(["Серия 1", "Трейлер"])
    }
  })

  it("leaves the field as it is when a page that is being read has no table yet and so no cards", async () => {
    site()
    vi.mocked(calls.testProfile).mockResolvedValue(ok(reply({ state: "reading", lines: [] })))
    const test = useRulesTest(() => 5)
    await test.readPage(PAGE, "", "search")
    expect(test.lines.value).toEqual([])
    expect(test.fetched.value).toEqual([])
  })

  it("joins the query to the channel of VK and sends the mode of the search as an option of the read", async () => {
    site("https://vk.com/video/@chan|", "vk_video")
    const test = useRulesTest(() => 5)
    await test.readPage("https://vk.com/video/@chan", "Тайтл 1 сезон", "get_all")
    expect(vi.mocked(calls.testProfile).mock.calls[0][0]).toMatchObject({
      url: "https://vk.com/video/@chan|Тайтл 1 сезон", options: { vk_search_mode: "get_all" },
    })
  })

  it("asks again, reading nothing, when the end of the read comes as an event, and not before or for another page", async () => {
    site()
    vi.mocked(calls.testProfile)
      .mockResolvedValueOnce(ok(reply({ state: "reading" })))
      .mockResolvedValue(ok(reply({ lines: [line("a", "Серия 1", 1)] })))
    const test = useRulesTest(() => 5)
    await test.readPage(PAGE, "", "search")
    expect(test.state.value).toBe("reading")
    const tables = useSourceTablesStore()
    tables.applyProgress({ url: PAGE, phase: "episodes", current: 2, total: 9 })
    tables.applyReady({ url: "https://other.test/p", read_at: "2026-11-01T10:00:00Z" })
    await flushPromises()
    expect(calls.testProfile).toHaveBeenCalledTimes(1)
    expect(test.progress.value).toMatchObject({ current: 2, total: 9 })
    tables.applyReady({ url: PAGE, read_at: "2026-11-01T10:00:00Z" })
    await flushPromises()
    expect(calls.testProfile).toHaveBeenCalledTimes(2)
    expect(vi.mocked(calls.testProfile).mock.calls[1][0]).toMatchObject({ url: PAGE, refresh: false })
    expect(test.state.value).toBe("ready")
    expect(test.lines.value).toHaveLength(1)
  })

  it("asks again when the connection comes back while a page is still being read", async () => {
    site()
    vi.mocked(calls.testProfile)
      .mockResolvedValueOnce(ok(reply({ state: "reading" })))
      .mockResolvedValue(ok(reply({ state: "failed", reason: '{"code":"read_failed"}' })))
    const test = useRulesTest(() => 5)
    await test.readPage(PAGE, "", "search")
    useSSE().connected.value = true
    await flushPromises()
    expect(vi.mocked(calls.testProfile).mock.calls[1][0]).toMatchObject({ refresh: false })
    expect(test.state.value).toBe("failed")
    expect(test.reason.value).toBe('{"code":"read_failed"}')
  })

  it("says in words that no site reads the address, and asks the core nothing", async () => {
    vi.mocked(address.typeAddress).mockResolvedValue({ kind: "other" })
    const test = useRulesTest(() => 5)
    await test.readPage("https://kinozal.tv/details.php?id=1", "", "search")
    expect(test.state.value).toBe("failed")
    expect(test.error.value).toBe(ru["rulesTest.notSite"])
    expect(calls.testProfile).not.toHaveBeenCalled()
  })

  it("shows the words of a refusal of the core", async () => {
    site()
    vi.mocked(calls.testProfile).mockResolvedValue({ ok: false, text: "Профиль не найден" })
    const test = useRulesTest(() => 5)
    await test.readPage(PAGE, "", "search")
    expect([test.state.value, test.error.value]).toEqual(["failed", "Профиль не найден"])
  })

  it("tries the profile on lines the window holds without reading a page", async () => {
    vi.mocked(calls.testProfile).mockResolvedValue(ok(reply({ url: "", lines: [line("0", "Серия 1", 1)] })))
    const test = useRulesTest(() => 5)
    await test.runLines(["Серия 1"])
    expect(calls.testProfile).toHaveBeenCalledWith({
      url: "", options: {}, profile_id: 5, series_season: null, refresh: true, lines: ["Серия 1"],
    })
    expect(test.state.value).toBe("ready")
    expect(test.lines.value[0].result?.episode).toBe(1)
    expect(address.typeAddress).not.toHaveBeenCalled()
  })

  it("keeps the newest answer when an older one comes late", async () => {
    let late!: (value: ReturnType<typeof ok<RulesTestReply>>) => void
    vi.mocked(calls.testProfile)
      .mockImplementationOnce(() => new Promise((resolve) => { late = resolve }))
      .mockResolvedValue(ok(reply({ lines: [line("0", "новая", 2)] })))
    const test = useRulesTest(() => 5)
    const older = test.runLines(["старая"])
    await test.runLines(["новая"])
    late(ok(reply({ lines: [line("0", "старая", 1)] })))
    await older
    expect(test.lines.value.map((each) => each.line)).toEqual(["новая"])
  })

  it("gives the file names of a series, or the words of the error", async () => {
    vi.mocked(calls.fetchFilenames).mockResolvedValueOnce(ok(["a.mkv", "b.mkv"]))
    const test = useRulesTest(() => 5)
    expect(await test.loadFilenames(3)).toEqual(["a.mkv", "b.mkv"])
    vi.mocked(calls.fetchFilenames).mockResolvedValueOnce({ ok: false, text: "Нет связи с сервером" })
    expect(await test.loadFilenames(3)).toBeNull()
    expect(test.error.value).toBe("Нет связи с сервером")
  })
})
