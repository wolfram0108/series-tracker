import { beforeEach, describe, expect, it, vi } from "vitest"
import { t } from "../i18n"
import { kindOf, typeAddress } from "./sourceAddress"
import * as calls from "./sourceCalls"

vi.mock("./sourceCalls")

const ok = <T>(data: T) => ({ ok: true as const, data })

beforeEach(() => vi.mocked(calls.resolveAddress).mockReset())

describe("typeAddress", () => {
  it("keeps a page of a site by the address the core names it with", async () => {
    vi.mocked(calls.resolveAddress).mockResolvedValue(ok({ site: "ruchime", canonical_url: "https://1.ruchime.org/release/a", source_type: "web_video" }))
    expect(await typeAddress("  https://1.ruchime.org/release/a?x=1 ")).toEqual({
      kind: "site", sourceType: "web_video", url: "https://1.ruchime.org/release/a",
    })
    expect(calls.resolveAddress).toHaveBeenCalledWith("https://1.ruchime.org/release/a?x=1")
  })

  it("keeps a VK channel with its query in one address", async () => {
    vi.mocked(calls.resolveAddress).mockResolvedValue(ok({ site: "vk_channel", canonical_url: null, source_type: "vk_video" }))
    const typed = await typeAddress("https://vk.com/video/@chan|Тайтл 1 сезон")
    expect(typed).toEqual({ kind: "site", sourceType: "vk_video", url: "https://vk.com/video/@chan|Тайтл 1 сезон" })
  })

  it("takes the query given apart from the channel, and refuses a channel that has none, the core asked nothing more", async () => {
    vi.mocked(calls.resolveAddress).mockResolvedValue(ok({ site: "vk_channel", canonical_url: "https://vkvideo.ru/@chan|", source_type: "vk_video" }))
    expect(await typeAddress("https://vk.com/video/@chan", " Тайтл ")).toEqual({
      kind: "site", sourceType: "vk_video", url: "https://vk.com/video/@chan|Тайтл",
    })
    for (const typed of ["https://vk.com/video/@chan", "https://vk.com/video/@chan|  "]) {
      expect(await typeAddress(typed)).toEqual({ kind: "refused", text: t("sources.vk.queryMissing") })
    }
  })

  it("tells the kind of an address with no rule of a source, for a window that asks while the user types", async () => {
    vi.mocked(calls.resolveAddress).mockResolvedValue(ok({ site: "vk_channel", canonical_url: "https://vkvideo.ru/@chan|", source_type: "vk_video" }))
    expect(await kindOf("https://vk.com/video/@chan")).toMatchObject({ kind: "site", sourceType: "vk_video" })
  })

  it("says that an address no site reads is another kind, and words a refusal", async () => {
    vi.mocked(calls.resolveAddress).mockResolvedValueOnce(ok({ site: null, canonical_url: null, source_type: null }))
    expect(await typeAddress("https://kinozal.tv/details.php?id=1")).toEqual({ kind: "other" })
    vi.mocked(calls.resolveAddress).mockResolvedValueOnce({ ok: false, text: "Нет связи с сервером" })
    expect(await typeAddress("x")).toEqual({ kind: "refused", text: "Нет связи с сервером" })
  })
})
