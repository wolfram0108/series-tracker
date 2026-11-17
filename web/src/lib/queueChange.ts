import type { components } from "../api/schema"
import { setBody, type QueueName, type QueueRow, type Scope } from "./output"

type Schemas = components["schemas"]

/** How long the user must stop pressing boxes before they go to the core: the one place the pause is set. */
export const QUEUE_PAUSE_MS = 3000

export type Queues = Schemas["QueuesReply"]
type Level = Schemas["Level"]

// A box of a queue the user ticked or cleared: the variant a queue takes, or a voice-over group taken at all. The window
// shows it at once and the core gets it with the boxes that follow it (lib/gather.ts); this is what the two share.
export type QueueChange = {
  scope: Scope
  queue: QueueName
  lever: string
  target: string
  value: boolean
} & ({ kind: "allow"; group: string; source_id: number; player: string } | { kind: "take"; group: string })

/** The level a window holds queues of, by its place: the key `useSeriesOutput` keeps them under. */
export const queueKey = (season: number | null, episode: number | null, compilation: string | null = null): string =>
  JSON.stringify([season, episode, compilation])

export const scopeKey = (scope: Scope): string => queueKey(scope.season ?? null, scope.episode ?? null, scope.compilation ?? null)

/** One change of one box has one key: a later press of the same box takes the place of the earlier. */
export const changeKey = (change: QueueChange): string => JSON.stringify([scopeKey(change.scope), change.lever, change.target])

export const levelOf = (scope: Scope): Level =>
  scope.compilation !== undefined ? "compilation" : scope.episode !== undefined ? "episode" : scope.season !== undefined ? "season" : "series"

/** The body of the lever of a change, as the core reads it. */
export const bodyOf = (change: QueueChange): Schemas["OutputSettingsSet"] =>
  setBody(change.scope, change.lever, change.target, change.value)

const rowsOf = (queues: Queues, change: QueueChange): readonly QueueRow[] | undefined => {
  if (change.queue === "reference") return queues.reference?.rows
  if (change.queue === "markup") return queues.markup?.rows
  if (change.queue === "subtitles") return queues.subtitles?.rows
  return queues.groups?.find((each) => each.group === change.group)?.rows
}

const isRowOf = (row: QueueRow, change: QueueChange): boolean =>
  change.kind === "allow" && row.group === change.group && row.source_id === change.source_id && row.player === change.player

/** What the core holds for the box of a change, as the queues of its level say; none when they do not list it. */
export function coreValue(queues: Queues | undefined, change: QueueChange): boolean | undefined {
  if (!queues) return undefined
  if (change.kind === "take") return queues.groups?.find((each) => each.group === change.group)?.take
  if (change.kind !== "allow") return undefined
  return rowsOf(queues, change)?.find((row) => isRowOf(row, change))?.allowed
}

/** The queues of a level as they are shown with the changes the core does not hold yet: only what a change touches is made
 *  anew, the rest stays the very object it was. A later change of the same box wins. */
export function withChanges(queues: Queues, changes: readonly QueueChange[]): Queues {
  let shown = queues
  for (const change of changes) {
    const level = levelOf(change.scope)
    if (change.kind === "take") {
      const groups = shown.groups ?? []
      if (!groups.some((each) => each.group === change.group && each.take !== change.value)) continue
      shown = { ...shown, groups: groups.map((each) => each.group === change.group ? { ...each, take: change.value, take_level: level } : each) }
      continue
    }
    const rows = rowsOf(shown, change)
    if (!rows?.some((row) => isRowOf(row, change) && row.allowed !== change.value)) continue
    const set = (list: readonly QueueRow[]): QueueRow[] =>
      list.map((row) => isRowOf(row, change) ? { ...row, allowed: change.value, allowed_level: level } : row)
    if (change.queue === "voice") {
      shown = { ...shown, groups: (shown.groups ?? []).map((each) => each.group === change.group ? { ...each, rows: set(rows) } : each) }
    } else if (change.queue === "reference" && shown.reference) {
      shown = { ...shown, reference: { ...shown.reference, rows: set(rows) } }
    } else if (change.queue === "markup" && shown.markup) {
      shown = { ...shown, markup: { ...shown.markup, rows: set(rows) } }
    } else if (change.queue === "subtitles" && shown.subtitles) {
      shown = { ...shown, subtitles: { ...shown.subtitles, rows: set(rows) } }
    }
  }
  return shown
}
