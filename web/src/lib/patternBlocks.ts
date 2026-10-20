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

// A template holds either several numbers or a single number with an attachment.
export function numberBlockCount(blocks: PatternBlock[]): number {
  return blocks.filter((b) => b.type === "number").length
}

export function attachmentBlockCount(blocks: PatternBlock[]): number {
  return blocks.filter((b) => isAttachmentType(b.type)).length
}

export function allowsNumber(blocks: PatternBlock[]): boolean {
  return attachmentBlockCount(blocks) === 0
}

// The attachment belongs to the only number of the template.
export function allowsAttachmentFor(blocks: PatternBlock[], dragged?: PatternBlock): boolean {
  const inTemplate = dragged ? blocks.includes(dragged) : false
  if (attachmentBlockCount(blocks) > (inTemplate ? 1 : 0)) return false
  if (inTemplate) return true
  return numberBlockCount(blocks) < 2
}

// The range filters a condition, the +/- shifts an extracted number.
export function attachmentAllowedInContext(type: string, context: string): boolean {
  if (type === "range") return context === "if"
  return context === "then"
}

// Reordering inside one template is always allowed; a block coming from
// elsewhere must fit the template it lands in.
export function canDrop(blocks: PatternBlock[], dragged: PatternBlock,
                       targetContext?: string): boolean {
  if (blocks.includes(dragged)) return true
  if (isAttachmentType(dragged.type)) {
    if (targetContext && !attachmentAllowedInContext(dragged.type, targetContext)) return false
    return allowsAttachmentFor(blocks, dragged)
  }
  if (dragged.type === "number") return allowsNumber(blocks)
  return true
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

// Removing the number must not leave the attachment orphaned.
export function removeBlockAt(blocks: PatternBlock[], index: number): void {
  blocks.splice(index, 1)
  normalizeAttachments(blocks)
}
