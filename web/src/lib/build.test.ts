// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { ownEntry, rebuilt } from "./build"

function page(html: string): Document {
  return new DOMParser().parseFromString(html, "text/html")
}

afterEach(() => vi.unstubAllGlobals())

describe("build", () => {
  it("knows the entry script of a built page and none of a page without one", () => {
    expect(ownEntry(page('<script type="module" crossorigin src="/assets/index-aRWTTFsr.js"></script>'))).toBe("/assets/index-aRWTTFsr.js")
    expect(ownEntry(page("<div></div>"))).toBeNull()
  })

  it("says the server serves another build only when its page does not name this one", async () => {
    const serve = (html: string, ok = true) => vi.stubGlobal("fetch", vi.fn(async () => ({ ok, text: async () => html })))
    serve('<script type="module" src="/assets/index-B78HgASI.js"></script>')
    expect(await rebuilt("/assets/index-aRWTTFsr.js")).toBe(true)
    expect(await rebuilt("/assets/index-B78HgASI.js")).toBe(false)
    serve("", false)
    expect(await rebuilt("/assets/index-aRWTTFsr.js")).toBe(false)
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline") }))
    expect(await rebuilt("/assets/index-aRWTTFsr.js")).toBe(false)
    expect(await rebuilt(null)).toBe(false)
  })
})
