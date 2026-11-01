import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { useSourceDrafts } from "./useSourceDrafts"
import { previewAddress, readPageNow, resolveAddress } from "./sourceCalls"
import { useSourceTablesStore } from "../stores/sourceTables"
import type { PreviewReply, ResolveReply } from "../lib/sources"

vi.mock("./sourceCalls", () => ({ resolveAddress: vi.fn(), previewAddress: vi.fn(), readPageNow: vi.fn() }))

const PAGE = "https://1.ruchime.org/release/test"
const resolve = vi.mocked(resolveAddress)
const preview = vi.mocked(previewAddress)

const site = (reply: ResolveReply) => resolve.mockResolvedValueOnce({ ok: true, data: reply })
const page = (over: Partial<PreviewReply> = {}) => ({
  ok: true as const,
  data: { url: PAGE, state: "reading", lines: [], ...over } as PreviewReply,
})
const summary = {
  source_id: 0, url: PAGE, status: "no_profile" as const, players: [
    { player: "vibix", player_label: "Vibix", rows: 23, groups: 2, heights: [1080], voices: [] },
  ],
}

beforeEach(() => {
  setActivePinia(createPinia())
  resolve.mockReset()
  preview.mockReset()
})

describe("useSourceDrafts", () => {
  it("makes a web source of an address a site reads and starts the read of its page", async () => {
    site({ site: "ruchime", canonical_url: PAGE })
    preview.mockResolvedValueOnce(page())
    const drafts = useSourceDrafts()
    expect(await drafts.add(`${PAGE}?x=1`)).toEqual({ kind: "added" })
    expect(preview).toHaveBeenCalledWith({ url: PAGE, options: {}, profile_id: null, refresh: true })
    expect(drafts.items.value).toHaveLength(1)
    expect(drafts.items.value[0]).toMatchObject({ sourceType: "web_video", url: PAGE, profileId: null })
    expect(drafts.items.value[0].view.state).toBe("reading")
    expect(drafts.bodies.value).toEqual([{
      url: PAGE, source_type: "web_video", parser_profile_id: null, vk_search_mode: "search", vibix_season: null, players_off: [],
    }])
  })

  it("keeps a VK channel as channel and query and asks with its mode", async () => {
    site({ site: "vk_channel", canonical_url: "https://vkvideo.ru/@kino|тайтл|search" })
    preview.mockResolvedValueOnce(page({ url: "https://vkvideo.ru/@kino|тайтл|search" }))
    const drafts = useSourceDrafts()
    await drafts.add("https://vkvideo.ru/@kino?q=тайтл")
    expect(drafts.items.value[0]).toMatchObject({ sourceType: "vk_video", url: "https://vkvideo.ru/@kino|тайтл" })
    expect(preview).toHaveBeenLastCalledWith(expect.objectContaining({ options: { vk_search_mode: "search" } }))
  })

  it("leaves an address that no site reads to the torrent path", async () => {
    site({ site: null, canonical_url: null })
    const drafts = useSourceDrafts()
    expect(await drafts.add("https://kinozal.tv/details.php?id=1")).toEqual({ kind: "other" })
    expect(drafts.items.value).toHaveLength(0)
    expect(preview).not.toHaveBeenCalled()
  })

  it("refuses the same source twice", async () => {
    site({ site: "ruchime", canonical_url: PAGE })
    preview.mockResolvedValueOnce(page())
    const drafts = useSourceDrafts()
    await drafts.add(PAGE)
    site({ site: "ruchime", canonical_url: PAGE })
    expect(await drafts.add(PAGE)).toEqual({ kind: "refused", text: "Этот источник уже в списке" })
    expect(drafts.items.value).toHaveLength(1)
  })

  it("asks the preview again, reading nothing, once the core says the page is read", async () => {
    site({ site: "ruchime", canonical_url: PAGE })
    preview.mockResolvedValueOnce(page())
    const drafts = useSourceDrafts()
    await drafts.add(PAGE)
    preview.mockResolvedValueOnce(page({ state: "ready", summary }))
    useSourceTablesStore().applyReady({ url: PAGE, read_at: "2026-11-01T10:00:00Z" })
    await flushPromises()
    expect(preview).toHaveBeenCalledTimes(2)
    expect(preview).toHaveBeenLastCalledWith(expect.objectContaining({ url: PAGE, refresh: false }))
    const view = drafts.items.value[0].view
    expect(view.state).toBe("noProfile")
    expect(view.players[0].chooseSeason).toBe(true)
  })

  it("shows the progress of the read from the events while it goes", async () => {
    site({ site: "ruchime", canonical_url: PAGE })
    preview.mockResolvedValueOnce(page())
    const drafts = useSourceDrafts()
    await drafts.add(PAGE)
    useSourceTablesStore().applyProgress({ url: PAGE, phase: "episodes", current: 4, total: 20 })
    expect(drafts.items.value[0].view).toMatchObject({ state: "reading", progress: { phase: "episodes", current: 4, total: 20 } })
    expect(preview).toHaveBeenCalledTimes(1)
  })

  it("takes up a read that ended before the answer of the request arrived", async () => {
    site({ site: "ruchime", canonical_url: PAGE })
    preview.mockImplementationOnce(async () => {
      useSourceTablesStore().applyReady({ url: PAGE, read_at: "2026-11-01T10:00:00Z" })
      return page()
    })
    preview.mockResolvedValueOnce(page({ state: "ready", summary }))
    const drafts = useSourceDrafts()
    await drafts.add(PAGE)
    expect(preview).toHaveBeenCalledTimes(2)
    expect(drafts.items.value[0].view.state).toBe("noProfile")
  })

  it("does not ask in a loop when the answer still says reading and no news came after it", async () => {
    useSourceTablesStore().applyReady({ url: PAGE, read_at: "old" })
    site({ site: "ruchime", canonical_url: PAGE })
    preview.mockResolvedValue(page())
    const drafts = useSourceDrafts()
    await drafts.add(PAGE)
    await flushPromises()
    expect(preview.mock.calls.length).toBeLessThanOrEqual(2)
  })

  it("recounts on a profile and reads again on another mode of a channel", async () => {
    site({ site: "vk_channel", canonical_url: "https://vkvideo.ru/@kino||search" })
    preview.mockResolvedValue(page({ url: "https://vkvideo.ru/@kino||search", state: "ready" }))
    const drafts = useSourceDrafts()
    await drafts.add("https://vkvideo.ru/@kino|")
    const id = drafts.items.value[0].id
    await drafts.change(id, { profileId: 4 })
    expect(preview).toHaveBeenLastCalledWith(expect.objectContaining({ profile_id: 4, refresh: false }))
    await drafts.change(id, { vkSearchMode: "get_all" })
    expect(preview).toHaveBeenLastCalledWith(expect.objectContaining({ options: { vk_search_mode: "get_all" }, refresh: true }))
    expect(drafts.bodies.value[0]).toMatchObject({ parser_profile_id: 4, vk_search_mode: "get_all" })
  })

  it("keeps the choices of a draft that need no new read without asking", async () => {
    site({ site: "ruchime", canonical_url: PAGE })
    preview.mockResolvedValueOnce(page({ state: "ready", summary }))
    const drafts = useSourceDrafts()
    await drafts.add(PAGE)
    await drafts.change(drafts.items.value[0].id, { vibixSeason: 2, playersOff: ["kodik"] })
    expect(preview).toHaveBeenCalledTimes(1)
    expect(drafts.bodies.value[0]).toMatchObject({ vibix_season: 2, players_off: ["kodik"] })
  })

  it("blocks the series while a source could not be read, and frees it once that source is removed", async () => {
    site({ site: "ruchime", canonical_url: PAGE })
    preview.mockResolvedValueOnce(page({ state: "failed", reason: '{"code":"read_failed"}' }))
    const drafts = useSourceDrafts()
    await drafts.add(PAGE)
    expect(drafts.failed.value).toBe(true)
    drafts.remove(drafts.items.value[0].id)
    expect(drafts.failed.value).toBe(false)
    expect(drafts.items.value).toHaveLength(0)
  })

  it("reads a page now though its table is fresh, and asks for the result once the read has ended", async () => {
    site({ site: "ruchime", canonical_url: PAGE })
    preview.mockResolvedValueOnce(page({ state: "ready", summary }))
    vi.mocked(readPageNow).mockReset().mockResolvedValue({ ok: true, data: { success: true } })
    const drafts = useSourceDrafts()
    await drafts.add(PAGE)
    const key = drafts.items.value[0].id
    await drafts.reread(key)
    expect(readPageNow).toHaveBeenCalledWith(PAGE, {})
    expect(drafts.items.value[0].view.state).toBe("reading")
    preview.mockResolvedValueOnce(page({ state: "ready", summary }))
    useSourceTablesStore().applyReady({ url: PAGE, read_at: "2026-11-01T10:00:00Z" })
    await flushPromises()
    expect(preview).toHaveBeenLastCalledWith({ url: PAGE, options: {}, profile_id: null, refresh: false })
    expect(drafts.items.value[0].view.state).toBe("noProfile")
  })

  it("says why the page could not be read now, and leaves the draft failed", async () => {
    site({ site: "ruchime", canonical_url: PAGE })
    preview.mockResolvedValueOnce(page({ state: "ready", summary }))
    vi.mocked(readPageNow).mockReset().mockResolvedValue({ ok: false, text: "Нет связи с сервером" })
    const drafts = useSourceDrafts()
    await drafts.add(PAGE)
    await drafts.reread(drafts.items.value[0].id)
    expect(drafts.error.value).toBe("Нет связи с сервером")
    expect(drafts.failed.value).toBe(true)
  })
})
