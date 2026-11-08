// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { inFrame } from "./layout"

const frame = () => new Promise<void>((done) => requestAnimationFrame(() => done()))

describe("inFrame", () => {
  it("runs every read of the frame before any write, so the layout is read once for all of them", async () => {
    const order: string[] = []
    for (const name of ["a", "b", "c"]) inFrame(() => order.push(`read ${name}`), () => order.push(`write ${name}`))
    expect(order).toEqual([])  // nothing before the frame
    await frame()
    expect(order).toEqual(["read a", "read b", "read c", "write a", "write b", "write c"])
  })

  it("gives each write the value of its own read", async () => {
    const written: number[] = []
    inFrame(() => 1, (value) => written.push(value))
    inFrame(() => 2, (value) => written.push(value * 10))
    await frame()
    expect(written).toEqual([1, 20])
  })
})
