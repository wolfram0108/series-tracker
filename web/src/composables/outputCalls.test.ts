import { beforeEach, describe, expect, it, vi } from "vitest"

const client = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() }))
vi.mock("../api/client", () => ({ api: client }))

import { fetchComposition, fetchOutput, fetchRenames, resetLever, setLever, switchOutput, syncSeries } from "./outputCalls"

beforeEach(() => Object.values(client).forEach((fn) => fn.mockReset()))

describe("the calls to the output of the core", () => {
  it("reads the layout of a video series by its path and gives it as it came", async () => {
    const layout = { series_id: 1, rev: 3, rows: [], sources: [], levels: [], settings: [] }
    client.GET.mockResolvedValueOnce({ data: layout })
    expect(await fetchComposition(1)).toEqual({ ok: true, data: layout })
    expect(client.GET).toHaveBeenCalledWith("/api/series/{series_id}/composition", { params: { path: { series_id: 1 } } })
  })

  it("reads the references of every episode of a series by its path", async () => {
    client.GET.mockResolvedValueOnce({ data: [{ season: 1, episode: 2, mark: "by_level" }] })
    expect(await fetchOutput(1)).toEqual({ ok: true, data: [{ season: 1, episode: 2, mark: "by_level" }] })
    expect(client.GET).toHaveBeenCalledWith("/api/series/{series_id}/output", { params: { path: { series_id: 1 } } })
  })

  it("sends a lever to the settings of the series with its body as it is", async () => {
    const body = { season: 2, episode: 5, lever: "voice.take", target: "group:AniStar", value: false }
    client.PUT.mockResolvedValueOnce({ data: { success: true } })
    expect(await setLever(1, body)).toEqual({ ok: true, data: { success: true } })
    expect(client.PUT).toHaveBeenCalledWith("/api/series/{series_id}/output/settings", { params: { path: { series_id: 1 } }, body })
  })

  it("resets the levers of a series with its body", async () => {
    const body = { season: 2, lever: "voice.take" }
    client.POST.mockResolvedValueOnce({ data: { success: true } })
    expect(await resetLever(1, body)).toEqual({ ok: true, data: { success: true } })
    expect(client.POST).toHaveBeenCalledWith("/api/series/{series_id}/output/settings/reset", { params: { path: { series_id: 1 } }, body })
  })

  it("switches an episode or a season by the path of the series with its body as it is", async () => {
    const body = { season: 2, episode: 5, off: true }
    client.PUT.mockResolvedValueOnce({ data: { success: true } })
    expect(await switchOutput(1, body)).toEqual({ ok: true, data: { success: true } })
    expect(client.PUT).toHaveBeenCalledWith("/api/series/{series_id}/output/switch", { params: { path: { series_id: 1 } }, body })
  })

  it("reads the names the files would get by the path of the series", async () => {
    const reply = { preview: [{ unique_id: "u1", season: 1, episode: 2, part: null, new_filename_preview: "Show s01e02.mp4" }] }
    client.GET.mockResolvedValueOnce({ data: reply })
    expect(await fetchRenames(1)).toEqual({ ok: true, data: reply })
    expect(client.GET).toHaveBeenCalledWith("/api/series/{series_id}/rename_preview", { params: { path: { series_id: 1 } } })
  })

  it("asks for the sync of the series by its path, the same run as the series card: with the download", async () => {
    client.POST.mockResolvedValueOnce({ data: { success: true } })
    expect(await syncSeries(1)).toEqual({ ok: true, data: { success: true } })
    expect(client.POST).toHaveBeenCalledWith("/api/series/{series_id}/scan", { params: { path: { series_id: 1 } } })
  })

  it("asks for the same sync with the failed attempts forgotten when the reset of failures is pressed", async () => {
    client.POST.mockResolvedValueOnce({ data: { success: true } })
    expect(await syncSeries(1, true)).toEqual({ ok: true, data: { success: true } })
    expect(client.POST).toHaveBeenCalledWith("/api/series/{series_id}/scan",
      { params: { path: { series_id: 1 }, query: { retry_failed: true } } })
  })

  it("says only that the action was refused when the refusal has no words", async () => {
    client.PUT.mockResolvedValueOnce({ error: { detail: [{ loc: ["body"], msg: "bad", type: "x" }] } })
    expect(await setLever(1, { lever: "voice.take" })).toEqual({ ok: false, text: "Не удалось выполнить действие" })
  })

  it("words a coded refusal of the core by its reason", async () => {
    client.PUT.mockResolvedValueOnce({ error: { success: false, error: '{"code":"unknown_lever","lever":"voice.x"}' } })
    expect(await setLever(1, { lever: "voice.x" })).toEqual({ ok: false, text: "Рычага «voice.x» нет в реестре" })
  })

  it("says the connection is lost when the request does not reach the core", async () => {
    client.GET.mockRejectedValueOnce(new TypeError("failed to fetch"))
    expect(await fetchOutput(1)).toEqual({ ok: false, text: "Нет связи с сервером" })
  })
})
