// Attachment blocks (range, +/-) are tails of a number: they change or filter
// the number they follow, so they only make sense glued to the last one.
// The engine enforces the same invariant (modules/rules/engine.py).

export interface PatternBlock {
  id: number | string
  type: string
  value?: string
}

export const ATTACHMENT_TYPES: readonly string[] = ["range", "add", "subtract"]

export function isAttachmentType(type: string): boolean {
  return ATTACHMENT_TYPES.includes(type)
}

export function lastNumberIndex(blocks: PatternBlock[]): number {
  for (let i = blocks.length - 1; i >= 0; i--) {
    if (blocks[i].type === "number") return i
  }
  return -1
}

// The attachment is glued when it stands right after the last number.
export function isGlued(blocks: PatternBlock[], index: number): boolean {
  const block = blocks[index]
  if (!block || !isAttachmentType(block.type)) return false
  const numIndex = lastNumberIndex(blocks)
  return numIndex !== -1 && index === numIndex + 1
}

// The number carries the attachment when the next block is one.
export function carriesAttachment(blocks: PatternBlock[], index: number): boolean {
  const block = blocks[index]
  const next = blocks[index + 1]
  if (!block || block.type !== "number" || !next) return false
  return isAttachmentType(next.type)
}

export function allowsAttachment(blocks: PatternBlock[], dragged?: PatternBlock): boolean {
  return !blocks.some((b) => isAttachmentType(b.type) && b !== dragged)
}

let autoIdCounter = 0

// Glues the single attachment to the last number; creates a number if none.
export function normalizeAttachments(blocks: PatternBlock[]): void {
  const attachment = blocks.find((b) => isAttachmentType(b.type))
  if (!attachment) return

  for (let i = blocks.length - 1; i >= 0; i--) {
    if (blocks[i] !== attachment && isAttachmentType(blocks[i].type)) blocks.splice(i, 1)
  }

  const numIndex = lastNumberIndex(blocks)
  if (numIndex !== -1 && blocks[numIndex + 1] === attachment) return

  blocks.splice(blocks.indexOf(attachment), 1)
  const numIndexAfter = lastNumberIndex(blocks)
  if (numIndexAfter === -1) {
    autoIdCounter += 1
    blocks.push({ id: `auto-${autoIdCounter}`, type: "number" })
    blocks.push(attachment)
    return
  }
  blocks.splice(numIndexAfter + 1, 0, attachment)
}
