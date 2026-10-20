import { describe, it, expect } from "vitest"
import {
  allowsAttachmentFor,
  allowsNumber,
  canDrop,
  carriesAttachment,
  isGlued,
  lastNumberIndex,
  normalizeAttachments,
  removeBlockAt,
  splitsAttachment,
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

describe("removeBlockAt", () => {
  it("keeps the attachment glued when the number is removed", () => {
    const blocks = [text("t"), num("n"), range("r")]
    removeBlockAt(blocks, 1)
    expect(blocks.map((b) => b.type)).toEqual(["text", "number", "range"])
    expect(isGlued(blocks, 2)).toBe(true)
  })

  it("removes the attachment itself without adding a number", () => {
    const blocks = [text("t"), num("n"), range("r")]
    removeBlockAt(blocks, 2)
    expect(blocks.map((b) => b.id)).toEqual(["t", "n"])
  })

  it("keeps the template shape when text follows the attachment", () => {
    // The number returns to the place of the attachment: the template keeps
    // its meaning, otherwise the pattern would match another title.
    const blocks = [num("n"), range("r"), text("t")]
    removeBlockAt(blocks, 0)
    expect(blocks.map((b) => b.type)).toEqual(["number", "range", "text"])
    expect(isGlued(blocks, 1)).toBe(true)
  })
})

describe("splitsAttachment", () => {
  it("sees a number with an attachment", () => {
    const blocks = [num("n"), range("r")]
    expect(splitsAttachment(blocks, blocks[0])).toBe(true)
  })

  it("leaves a plain number alone", () => {
    const blocks = [num("n"), text("t")]
    expect(splitsAttachment(blocks, blocks[0])).toBe(false)
    expect(splitsAttachment(blocks, range("r"))).toBe(false)
  })
})

describe("glued state", () => {  it("marks the attachment and the number it hangs on", () => {
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

describe("guards", () => {
  it("withholds the number when the template already has an attachment", () => {
    const blocks = [num("n"), range("r")]
    expect(allowsNumber(blocks)).toBe(false)
    expect(allowsNumber([num("n"), num("m")])).toBe(true)
  })

  it("withholds the attachment when the template already has two numbers", () => {
    const blocks = [num("n"), num("m")]
    expect(allowsAttachmentFor(blocks)).toBe(false)
    expect(allowsAttachmentFor([num("n")])).toBe(true)
  })

  it("withholds a second attachment", () => {
    const blocks = [num("n"), range("r")]
    expect(allowsAttachmentFor(blocks)).toBe(false)
  })

  it("drops a number into a template with an attachment", () => {
    const target = [num("n"), range("r")]
    expect(canDrop(target, num("x"))).toBe(false)
  })

  it("drops an attachment into a template with two numbers", () => {
    const target = [num("n"), num("m")]
    expect(canDrop(target, range("r"))).toBe(false)
  })

  it("allows reordering inside one template", () => {
    const target = [num("n"), range("r")]
    expect(canDrop(target, target[0])).toBe(true)
    expect(canDrop(target, target[1])).toBe(true)
  })

  it("allows a fresh number into a template without attachment", () => {
    const target = [num("n"), num("m")]
    expect(canDrop(target, num("x"))).toBe(true)
  })

  it("keeps the range out of actions and the +/- out of conditions", () => {
    const target = [num("n")]
    expect(canDrop(target, range("r"), "if")).toBe(true)
    expect(canDrop(target, range("r"), "then")).toBe(false)
    expect(canDrop(target, add("a"), "then")).toBe(true)
    expect(canDrop(target, add("a"), "if")).toBe(false)
  })
})
