import { describe, it, expect } from "vitest"
import {
  allowsAttachment,
  carriesAttachment,
  isGlued,
  lastNumberIndex,
  normalizeAttachments,
  type PatternBlock,
} from "./patternBlocks"

const num = (id: string): PatternBlock => ({ id, type: "number" })
const text = (id: string): PatternBlock => ({ id, type: "text", value: " " })
const range = (id: string): PatternBlock => ({ id, type: "range", value: "1-10" })
const add = (id: string): PatternBlock => ({ id, type: "add", value: "5" })

describe("normalizeAttachments", () => {
  it("keeps a correctly glued attachment untouched", () => {
    const blocks = [text("t"), num("n"), range("r")]
    normalizeAttachments(blocks)
    expect(blocks.map((b) => b.id)).toEqual(["t", "n", "r"])
  })

  it("glues a loose attachment to the last number", () => {
    const blocks = [text("t"), num("n"), range("r"), text("u")]
    normalizeAttachments(blocks)
    expect(blocks.map((b) => b.id)).toEqual(["t", "n", "r", "u"])
  })

  it("moves an attachment that follows a non-number block", () => {
    const blocks = [num("n"), add("a"), text("t"), num("m")]
    normalizeAttachments(blocks)
    expect(blocks.map((b) => b.id)).toEqual(["n", "t", "m", "a"])
  })

  it("creates a number when the template has none", () => {
    const blocks: PatternBlock[] = [text("t"), range("r")]
    normalizeAttachments(blocks)
    expect(blocks.map((b) => b.type)).toEqual(["text", "number", "range"])
    expect(blocks[1].id).toBeTruthy()
  })

  it("keeps a single attachment when duplicates exist", () => {
    const blocks = [num("n"), range("r"), add("a")]
    normalizeAttachments(blocks)
    expect(blocks.filter((b) => ["range", "add", "subtract"].includes(b.type))).toHaveLength(1)
  })

  it("leaves templates without attachments alone", () => {
    const blocks = [num("n"), text("t")]
    normalizeAttachments(blocks)
    expect(blocks.map((b) => b.id)).toEqual(["n", "t"])
  })
})

describe("glued state", () => {
  it("marks the attachment and the number it hangs on", () => {
    const blocks = [text("t"), num("n"), range("r")]
    expect(isGlued(blocks, 2)).toBe(true)
    expect(carriesAttachment(blocks, 1)).toBe(true)
    expect(isGlued(blocks, 0)).toBe(false)
    expect(carriesAttachment(blocks, 0)).toBe(false)
  })

  it("does not mark an attachment standing away from the number", () => {
    const blocks = [text("t"), range("r"), num("n")]
    expect(isGlued(blocks, 1)).toBe(false)
    expect(lastNumberIndex(blocks)).toBe(2)
  })
})

describe("allowsAttachment", () => {
  it("permits one attachment per template", () => {
    const blocks = [num("n"), range("r")]
    expect(allowsAttachment(blocks)).toBe(false)
    expect(allowsAttachment(blocks, blocks[1])).toBe(true)
    expect(allowsAttachment([num("n")])).toBe(true)
  })
})
