import { describe, expect, it } from "vitest"
import { nextTick, ref } from "vue"
import { shared, sharing } from "./sharing"

describe("shared", () => {
  it("is the earlier value itself when nothing differs, at any depth", () => {
    const before = { rows: [{ n: 1, cells: ["a", "b"] }, { n: 2, cells: [] }], tone: { color: null } }
    expect(shared(before, JSON.parse(JSON.stringify(before)))).toBe(before)
  })

  it("keeps the parts that did not change and takes the ones that did", () => {
    const before = [{ n: 1, cells: ["a"] }, { n: 2, cells: ["b"] }, { n: 3, cells: ["c"] }]
    const after = shared(before, [{ n: 1, cells: ["a"] }, { n: 2, cells: ["B"] }, { n: 3, cells: ["c"] }])
    expect(after).not.toBe(before)
    expect([after[0] === before[0], after[1] === before[1], after[2] === before[2]]).toEqual([true, false, true])
    expect(after[1]).toEqual({ n: 2, cells: ["B"] })
  })

  it("tells a part that went, came or changed its kind", () => {
    const before = { a: 1, b: [1, 2], c: { d: 1 } }
    expect(shared(before, { a: 1, b: [1, 2] })).toEqual({ a: 1, b: [1, 2] })
    expect(shared(before, { a: 1, b: [1, 2] })).not.toBe(before)
    expect(shared(before, { a: 1, b: [1, 2], c: { d: 1 }, e: undefined })).not.toBe(before)
    expect(shared(before, { a: 1, b: [1, 2, 3], c: { d: 1 } }).c).toBe(before.c)
    expect(shared(before, { a: 1, b: { 0: 1, 1: 2 }, c: null })).toEqual({ a: 1, b: { 0: 1, 1: 2 }, c: null })
    expect(shared(undefined, before)).toBe(before)
  })

  it("leaves what is no plain object or array as it is", () => {
    const map = new Map([["a", 1]])
    const before = { cuts: new Map([["a", 1]]) }
    expect(shared(before, { cuts: map }).cuts).toBe(map)
  })
})

describe("sharing", () => {
  it("counts anew with every change and gives the same object for every part that came out the same", async () => {
    const plan = ref({ rev: 1, moved: 2 })
    const cards = sharing(() => [1, 2, 3].map((n) => ({ n, state: plan.value.moved === n && plan.value.rev > 1 ? "done" : "wait" })))
    const first = cards.value
    plan.value = { rev: 2, moved: 2 }
    await nextTick()
    const second = cards.value
    expect(second).not.toBe(first)
    expect([second[0] === first[0], second[1] === first[1], second[2] === first[2]]).toEqual([true, false, true])
    plan.value = { rev: 3, moved: 2 }
    expect(cards.value).toBe(second)
  })
})
