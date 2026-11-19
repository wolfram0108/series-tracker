import { beforeEach, describe, expect, it, vi } from "vitest"

const client = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() }))
vi.mock("../api/client", () => ({ api: client }))

import { fetchComposition, resetFailures, resetLever, setLever, setLevers, switchOutput, syncSeries } from "./outputCalls"

beforeEach(() => Object.values(client).forEach((fn) => fn.mockReset()))

describe("the calls to the output of the core", () => {
  it("reads the layout of a video series by its path and gives it as it came", async () => {
    const layout = { series_id: 1, rev: 3, rows: [], sources: [], levels: [], settings: [] }
    client.GET.mockResolvedValueOnce({ data: layout })
    expect(await fetchComposition(1)).toEqual({ ok: true, data: layout })
    expect(client.GET).toHaveBeenCalledWith("/api/series/{series_id}/composition", { params: { path: { series_id: 1 } } })
  })

  it("asks for the part of the layout a change touched by its name and episodes", async () => {
    client.GET.mockResolvedValueOnce({ data: { series_id: 1, rev: 4, part: "levers" } })
    await fetchComposition(1, "levers", ["1:2", "1:3"])
    expect(client.GET).toHaveBeenCalledWith("/api/series/{series_id}/composition",
      { params: { path: { series_id: 1 }, query: { part: "levers", episodes: ["1:2", "1:3"] } } })
  })

  it("sends a lever to the settings of the series with its body as it is", async () => {
    const body = { season: 2, episode: 5, lever: "voice.take", target: "group:AniStar", value: false }
    client.PUT.mockResolvedValueOnce({ data: { success: true } })
    expect(await setLever(1, body)).toEqual({ ok: true, data: { success: true } })
    expect(client.PUT).toHaveBeenCalledWith("/api/series/{series_id}/output/settings", { params: { path: { series_id: 1 } }, body })
  })

  it("sends several levers in one command to the batch of the series", async () => {
    const items = [{ season: 1, lever: "reference.allow", target: "variant:a", value: false }, { season: 1, lever: "voice.take", target: "group:A", value: true }]
    client.PUT.mockResolvedValueOnce({ data: { success: true } })
    expect(await setLevers(1, items)).toEqual({ ok: true, data: { success: true } })
    expect(client.PUT).toHaveBeenCalledWith("/api/series/{series_id}/output/settings/batch", { params: { path: { series_id: 1 } }, body: { items } })
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


  it("asks for the sync of the series by its path, the same run as the series card: with the download", async () => {
    client.POST.mockResolvedValueOnce({ data: { success: true } })
    expect(await syncSeries(1)).toEqual({ ok: true, data: { success: true } })
    expect(client.POST).toHaveBeenCalledWith("/api/series/{series_id}/scan", { params: { path: { series_id: 1 } } })
  })

  it("names the season or the episode a sync covers, and the whole series when it names none", async () => {
    client.POST.mockResolvedValue({ data: { success: true } })
    await syncSeries(1, { season: 1, episode: 3 })
    expect(client.POST).toHaveBeenLastCalledWith("/api/series/{series_id}/scan", {
      params: { path: { series_id: 1 } }, body: { season: 1, episode: 3 } })
    await syncSeries(1)
    expect(client.POST).toHaveBeenLastCalledWith("/api/series/{series_id}/scan", { params: { path: { series_id: 1 } } })
  })

  it("asks for the reset of the failed attempts by its own path, not for a sync", async () => {
    client.POST.mockResolvedValueOnce({ data: { success: true, reset: 2 } })
    expect(await resetFailures(1)).toEqual({ ok: true, data: { success: true, reset: 2 } })
    expect(client.POST).toHaveBeenCalledWith("/api/series/{series_id}/failures/reset", { params: { path: { series_id: 1 } } })
  })

  it("names the level whose failed attempts are reset, so that the others stay", async () => {
    client.POST.mockResolvedValueOnce({ data: { success: true, reset: 1 } })
    await resetFailures(1, { season: 1, episode: 2, voice: "AniStar" })
    expect(client.POST).toHaveBeenCalledWith("/api/series/{series_id}/failures/reset", {
      params: { path: { series_id: 1 } }, body: { season: 1, episode: 2, voice: "AniStar" } })
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
    expect(await fetchComposition(1)).toEqual({ ok: false, text: "Нет связи с сервером" })
  })
})
