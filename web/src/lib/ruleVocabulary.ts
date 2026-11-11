import { t } from "../i18n"
import { attachmentBlockCount, isAttachmentType, numberBlockCount, type PatternBlock } from "./patternBlocks"

/** The vocabulary of the rules constructor the panel offers: one place for every card of it. Each list is written out
 *  in full, so tests/test_rules_vocabulary.py checks it against the engine (modules/rules/engine.py) as it is. */
export const CONDITION_TYPES = ["contains", "not_contains"] as const
export const EXTRACT_TYPES = ["extract_single", "extract_range", "extract_season"] as const
export const ASSIGN_TYPES = ["assign_voiceover", "assign_episode", "assign_season", "assign_part", "assign_quality",
  "assign_resolution", "assign_range"] as const
export const ACTION_TYPES = ["exclude", "extract_single", "extract_range", "extract_season", "assign_voiceover",
  "assign_episode", "assign_season", "assign_part", "assign_quality", "assign_resolution", "assign_range"] as const
/** The assignments whose value is a number. */
export const NUMBER_ASSIGNS = ["assign_episode", "assign_season", "assign_part"] as const

export type ActionType = (typeof ACTION_TYPES)[number]

export const isExtract = (type: string): boolean => (EXTRACT_TYPES as readonly string[]).includes(type)
export const isAssign = (type: string): boolean => (ASSIGN_TYPES as readonly string[]).includes(type)
export const isNumberAssign = (type: string): boolean => (NUMBER_ASSIGNS as readonly string[]).includes(type)

/** What a field of the editor holds, as text: the value of every assignment is text for the core and for the editor,
 *  also the number of a season, an episode or a part (a field of numbers bound as a model would hand over a number). */
export const fieldText = (event: Event): string => (event.target as HTMLInputElement).value

/** The options of the selects of the constructor, worded by the catalog. */
export const conditionOptions = () => CONDITION_TYPES.map((value) => ({ value, label: t(`rules.condition.${value}`) }))
export const actionOptions = () => ACTION_TYPES.map((value) => ({ value, label: t(`rules.action.${value}`) }))
export const actionLabel = (type: string): string =>
  (ACTION_TYPES as readonly string[]).includes(type) ? t(`rules.action.${type as ActionType}`) : t("rules.action.value")

/** A block the palette offers; `disabled` with its `reason` when the template it would go to cannot take it. */
export interface PaletteItem { type: string; label: string; title: string; disabled?: boolean; reason?: string }

export const BLOCK_PALETTE: PaletteItem[] = [
  { type: "text", label: t("rules.block.text.label"), title: t("rules.block.text.title") },
  { type: "or", label: t("rules.block.or.label"), title: t("rules.block.or.title") },
  { type: "number", label: t("rules.block.number.label"), title: t("rules.block.number.title") },
  { type: "range", label: t("rules.block.range.label"), title: t("rules.block.range.title") },
  { type: "add", label: t("rules.block.add.label"), title: t("rules.block.add.title") },
  { type: "subtract", label: t("rules.block.subtract.label"), title: t("rules.block.subtract.title") },
  { type: "whitespace", label: t("rules.block.whitespace.label"), title: t("rules.block.whitespace.title") },
  { type: "any_text", label: t("rules.block.any_text.label"), title: t("rules.block.any_text.title") },
  { type: "start_of_line", label: t("rules.block.start_of_line.label"), title: t("rules.block.start_of_line.title") },
  { type: "end_of_line", label: t("rules.block.end_of_line.label"), title: t("rules.block.end_of_line.title") },
]

/** The word of a block that has no value of its own. */
export const blockLabel = (type: string): string =>
  BLOCK_PALETTE.find((item) => item.type === type)?.label || t("rules.block.unknown")

// A template holds either several numbers or a single number with an attachment: the attachment belongs to that one
// number, so the two cannot be combined.
function disabledReason(blocks: PatternBlock[], type: string): string {
  const hasAttachment = attachmentBlockCount(blocks) > 0
  if (isAttachmentType(type)) {
    if (hasAttachment) return t("rules.block.attachmentTaken")
    if (numberBlockCount(blocks) >= 2) return t("rules.block.twoNumbers")
    return ""
  }
  if (type === "number" && hasAttachment) return t("rules.block.attachmentTaken")
  return ""
}

/** The palette for a template: the range only filters a condition («ЕСЛИ»), the ± only shifts an extracted number. */
export function paletteFor(blocks: PatternBlock[], context: "if" | "then"): PaletteItem[] {
  const items = context === "if"
    ? BLOCK_PALETTE.filter((item) => !["add", "subtract"].includes(item.type))
    : BLOCK_PALETTE.filter((item) => item.type !== "range")
  return items.map((item) => {
    const reason = disabledReason(blocks, item.type)
    return reason ? { ...item, disabled: true, reason } : item
  })
}

/** A new block of the kind of a palette item, with the value it starts with. */
export function cloneBlock(original: { type: string }): PatternBlock {
  const block: PatternBlock = { id: Date.now() + Math.random(), type: original.type }
  if (original.type === "text") block.value = ""
  if (original.type === "add" || original.type === "subtract") block.value = "1"
  if (original.type === "range") block.value = "1, 3-10"
  return block
}
