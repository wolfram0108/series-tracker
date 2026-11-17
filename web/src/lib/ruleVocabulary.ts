import { t } from "../i18n"
import { LANGUAGES } from "./languages"
import { attachmentBlockCount, isAttachmentType, numberBlockCount, type PatternBlock } from "./patternBlocks"

/** The vocabulary of the rules constructor the panel offers: one place for every card of it. Each list is written out
 *  in full, so tests/test_rules_vocabulary.py checks it against the engine (modules/rules/engine.py) as it is. */
export const CONDITION_TYPES = ["contains", "not_contains"] as const
export const EXTRACT_TYPES = ["extract_single", "extract_range", "extract_season"] as const
export const ASSIGN_TYPES = ["assign_voiceover", "assign_episode", "assign_season", "assign_part", "assign_quality",
  "assign_resolution", "assign_range"] as const
export const ACTION_TYPES = ["exclude", "extract_single", "extract_range", "extract_season", "assign_voiceover",
  "assign_episode", "assign_season", "assign_part", "assign_quality", "assign_resolution", "assign_range"] as const
/** The actions of the editor of a release (modules/rules/engine.py EDITOR_ASSIGN): a track into a folder of a voice-over or of
 *  subtitles, its language, its type of subtitles. Offered by the cards of the tabs of the tracks of a release only: a row
 *  of a site has none of them. */
export const EDITOR_ACTIONS = ["assign_video_tag", "assign_voice_folder", "assign_subtitle_folder", "assign_language",
  "assign_subtitle_type"] as const
/** The values of the type of subtitles (core/contracts/torrent.py SUBTITLE_TYPES). */
export const SUBTITLE_TYPES = ["full", "signs"] as const
/** The assignments whose value is a number. */
export const NUMBER_ASSIGNS = ["assign_episode", "assign_season", "assign_part"] as const

export type ActionType = (typeof ACTION_TYPES)[number] | (typeof EDITOR_ACTIONS)[number]

export const isExtract = (type: string): boolean => (EXTRACT_TYPES as readonly string[]).includes(type)
export const isAssign = (type: string): boolean =>
  (ASSIGN_TYPES as readonly string[]).includes(type) || (EDITOR_ACTIONS as readonly string[]).includes(type)
export const isNumberAssign = (type: string): boolean => (NUMBER_ASSIGNS as readonly string[]).includes(type)

/** What a field of the editor holds, as text: the value of every assignment is text for the core and for the editor,
 *  also the number of a season, an episode or a part (a field of numbers bound as a model would hand over a number). */
export const fieldText = (event: Event): string => (event.target as HTMLInputElement).value

/** The options of the selects of the constructor, worded by the catalog. */
export const conditionOptions = () => CONDITION_TYPES.map((value) => ({ value, label: t(`rules.condition.${value}`) }))
/** Where a constructor stands: the section «Правила» of the series, or a tab of the torrent editor — «Файлы», the sound
 *  («Озвучки», «Озв. конт.»), the subtitles («Субтитры», «Суб. конт.»). */
export type RulePlace = "series" | "files" | "voices" | "subtitles"
/** Where each action applies: a card offers in «ТО» only the actions of its place. The torrent editor places and never
 *  leaves out (leaving out is the constructor's of the series); «Файлы» gives a file its season and episode; the tabs of the
 *  sound and of the subtitles give a track its folder, its language and (subtitles) its type. */
export const ACTION_PLACES: Record<ActionType, readonly RulePlace[]> = {
  exclude: ["series"],
  extract_single: ["series", "files"],
  extract_range: ["series", "files"],
  extract_season: ["series", "files"],
  assign_voiceover: ["series"],
  assign_episode: ["series", "files"],
  assign_season: ["series", "files"],
  assign_part: ["series"],
  assign_quality: ["series"],
  assign_resolution: ["series"],
  assign_range: ["series"],
  assign_video_tag: ["files"],
  assign_voice_folder: ["voices"],
  assign_subtitle_folder: ["subtitles"],
  assign_language: ["voices", "subtitles"],
  assign_subtitle_type: ["subtitles"],
}
/** The actions a card of a place offers. */
export const actionOptions = (place: RulePlace = "series") => [...ACTION_TYPES, ...EDITOR_ACTIONS]
  .filter((value) => ACTION_PLACES[value].includes(place))
  .map((value) => ({ value, label: t(`rules.action.${value}`) }))
export const actionLabel = (type: string): string =>
  ([...ACTION_TYPES, ...EDITOR_ACTIONS] as readonly string[]).includes(type) ? t(`rules.action.${type as ActionType}`) : t("rules.action.value")

/** The values an action of the editor is chosen from: the languages of the project, the types of subtitles; none for the rest
 *  (a folder is any name). */
export function choicesOf(type: string): { value: string; label: string }[] | null {
  if (type === "assign_language") return LANGUAGES.map((code) => ({ value: code, label: `${t(`voices.language.${code}`)} (${code})` }))
  if (type === "assign_subtitle_type") return SUBTITLE_TYPES.map((value) => ({ value, label: t(`torrent.type.${value}`) }))
  return null
}

/** «В каталог озвучки», «В каталог субтитров»: one «ТО» of several fields, the name of the folder, its language and — a folder of
 *  subtitles — its type (full, signs). The value is `{"name", "language", "type"}` as JSON, or the name alone when nothing else
 *  is given (modules/rules/engine.py folder_value). */
export const FOLDER_ACTIONS = ["assign_voice_folder", "assign_subtitle_folder"] as const
export const isFolderAction = (type: string): boolean => (FOLDER_ACTIONS as readonly string[]).includes(type)
export interface FolderValue { name: string; language: string; type?: string }
export function folderValueOf(raw: unknown): FolderValue {
  const text = String(raw ?? "").trim()
  if (text.startsWith("{")) {
    try {
      const value = JSON.parse(text) as Partial<FolderValue>
      return { name: String(value.name ?? ""), language: String(value.language ?? ""), type: String(value.type ?? "") }
    } catch {
      return { name: text, language: "", type: "" }
    }
  }
  return { name: text, language: "", type: "" }
}
export const folderValueText = (value: FolderValue): string => value.language || value.type
  ? JSON.stringify({ name: value.name, ...(value.language && { language: value.language }), ...(value.type && { type: value.type }) })
  : value.name
/** The languages a folder is given, by their code as the folder keeps it (rus, eng), «—» first: the language may be said by
 *  another rule or the file. */
export const folderLanguages = (): { value: string; label: string }[] =>
  [{ value: "", label: "—" }, ...LANGUAGES.map((code) => ({ value: code, label: code }))]

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
