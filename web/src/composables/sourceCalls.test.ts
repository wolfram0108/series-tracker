import { beforeEach, describe, expect, it, vi } from "vitest"

const client = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() }))
vi.mock("../api/client", () => ({ api: client }))

import { changeSource, removeSource, resolveAddress } from "./sourceCalls"

beforeEach(() => Object.values(client).forEach((fn) => fn.mockReset()))

describe("the calls to the sources of the core", () => {
  it("words the refusal to delete the only source of a series", async () => {
    client.DELETE.mockResolvedValueOnce({ error: { success: false, error: '{"code":"last_source","series_id":1,"source_id":7}' } })
    expect(await removeSource(1, 7)).toEqual({ ok: false, text: "Единственный источник сериала удалить нельзя" })
  })

  it("words a refusal that is no coded reason as the core said it", async () => {
    client.PUT.mockResolvedValueOnce({ error: { success: false, error: "источник 9 не найден у сериала 1" } })
    expect(await changeSource(1, 9, {})).toEqual({ ok: false, text: "источник 9 не найден у сериала 1" })
  })

  it("says only that the request was refused when the body of the refusal has no words", async () => {
    client.POST.mockResolvedValueOnce({ error: { detail: [{ loc: ["body"], msg: "bad", type: "x" }] } })
    expect(await resolveAddress("x")).toEqual({ ok: false, text: "Запрос отклонён" })
  })

  it("says the connection is lost when the request does not reach the core", async () => {
    client.POST.mockRejectedValueOnce(new TypeError("failed to fetch"))
    expect(await resolveAddress("x")).toEqual({ ok: false, text: "Нет связи с сервером" })
  })

  it("gives the data of an answer as it came", async () => {
    client.POST.mockResolvedValueOnce({ data: { site: "ruchime", canonical_url: "https://1.ruchime.org/release/test", source_type: "web_video" } })
    expect(await resolveAddress("https://1.ruchime.org/release/test?x=1")).toEqual({
      ok: true, data: { site: "ruchime", canonical_url: "https://1.ruchime.org/release/test", source_type: "web_video" },
    })
    expect(client.POST).toHaveBeenCalledWith("/api/sources/resolve", {
      body: { url: "https://1.ruchime.org/release/test?x=1" },
    })
  })
})
