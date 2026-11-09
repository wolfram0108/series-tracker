// Attachment blocks (range, +/-) are tails of a number: they only make sense
// glued to the only number of the template. The engine requires the same.

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

// The block or joins the texts on its two sides into one place of the title (any of them): it stands between two
// texts, and a chain "A" or "B" or "C" is one group. The engine refuses it anywhere else.
export function orInPlace(blocks: PatternBlock[], index: number): boolean {
  return blocks[index]?.type === "or" && blocks[index - 1]?.type === "text" && blocks[index + 1]?.type === "text"
}

// A block or that has no text on one of its sides yet: the template is not finished.
export function isDanglingOr(blocks: PatternBlock[], index: number): boolean {
  return blocks[index]?.type === "or" && !orInPlace(blocks, index)
}

// A block touches the one before it: an attachment on its number, or a member of a group of the block or.
export function joinsPrevious(blocks: PatternBlock[], index: number): boolean {
  const type = blocks[index]?.type
  if (type === "or") return orInPlace(blocks, index)
  if (type === "text") return orInPlace(blocks, index - 1)
  return isGlued(blocks, index)
}

// A block touches the one after it: a number that carries its attachment, or a member of a group of the block or.
export function joinsNext(blocks: PatternBlock[], index: number): boolean {
  const type = blocks[index]?.type
  if (type === "or") return orInPlace(blocks, index)
  if (type === "text") return orInPlace(blocks, index + 1)
  return carriesAttachment(blocks, index)
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

// The number and its attachment are one unit: they are not split between templates.
export function splitsAttachment(source: PatternBlock[], dragged: PatternBlock): boolean {
  if (dragged.type !== "number") return false
  const index = source.indexOf(dragged)
  return index !== -1 && carriesAttachment(source, index)
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

  const at = blocks.indexOf(attachment)
  blocks.splice(at, 1)
  const numIndexAfter = lastNumberIndex(blocks)
  if (numIndexAfter === -1) {
    autoIdCounter += 1
    // The number takes the place of the attachment: the template keeps its shape.
    blocks.splice(at, 0, { id: `auto-${autoIdCounter}`, type: "number" }, attachment)
    return
  }
  blocks.splice(numIndexAfter + 1, 0, attachment)
}

// Removing the number must not leave the attachment orphaned.
export function removeBlockAt(blocks: PatternBlock[], index: number): void {
  blocks.splice(index, 1)
  normalizeAttachments(blocks)
}

// The check of a drag between templates: the template that receives the block is taken from the drop target, not from
// the list the drag starts in (the sortable fires the check on the source).
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function allowMove(evt: any): boolean {
  const dragged = evt.draggedContext?.element as PatternBlock | undefined
  const source = evt.draggedContext?.list as PatternBlock[] | undefined
  const target = evt.relatedContext?.list as PatternBlock[] | undefined
  if (!dragged || !target) return true
  // A number and its attachment are one unit and are not split apart.
  if (source && source !== target && splitsAttachment(source, dragged)) return false
  const targetContext = evt.to?.getAttribute?.("data-context") ?? undefined
  return canDrop(target, dragged, targetContext)
}
