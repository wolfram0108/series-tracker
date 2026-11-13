import type { QueueRow } from "./output"
import { isVariantOf, type Held } from "./plan"

// Variants of a queue the user declared equal (Р-99): the groups the rows stand in and what each edit of them makes of the
// value of the lever `reference.same` / `markup.same` — a list of groups, each a list of the variants as an order names them.
// The core numbers the groups by where each first stands in the queue; an edit is told the whole value, as an order is.

/** A variant as the levers name it: its voice-over group, its source and its player. */
export interface SameVariant { group: string; source_id: number; player: string }
export type SameGroups = SameVariant[][]

export const rowKey = (row: QueueRow): string => JSON.stringify([row.group, row.source_id, row.player])
const variantOf = (row: QueueRow): SameVariant => ({ group: row.group, source_id: row.source_id, player: row.player })

/** The rows by the number of their group, in the order of the numbers; a row in no group is in none of them. */
function byNumber(rows: readonly QueueRow[]): Map<number, QueueRow[]> {
  const found = new Map<number, QueueRow[]>()
  for (const row of rows) if (row.same != null) found.set(row.same, [...(found.get(row.same) ?? []), row])
  return new Map([...found].sort(([a], [b]) => a - b))
}

const asValue = (groups: Iterable<readonly QueueRow[]>): SameGroups =>
  [...groups].filter((rows) => rows.length > 1).map((rows) => rows.map(variantOf))

/** The groups the queue shows, as the value of the lever that makes them. */
export const groupsOf = (rows: readonly QueueRow[]): SameGroups => asValue(byNumber(rows).values())

/** The groups after the picked rows are made one group, with every group any of them stands in. Fewer than two rows
 *  come out of it: nothing changes. */
export function joined(rows: readonly QueueRow[], picked: ReadonlySet<string>): SameGroups {
  const chosen = rows.filter((row) => picked.has(rowKey(row)))
  const touched = new Set(chosen.map((row) => row.same).filter((number): number is number => number != null))
  const one = rows.filter((row) => picked.has(rowKey(row)) || (row.same != null && touched.has(row.same)))
  if (one.length < 2) return groupsOf(rows)
  const rest = [...byNumber(rows)].filter(([number]) => !touched.has(number)).map(([, group]) => group)
  return asValue([...rest, one])
}

/** The groups after a row leaves its group; a group left with one row is gone. */
export function left(rows: readonly QueueRow[], row: QueueRow): SameGroups {
  const key = rowKey(row)
  return asValue([...byNumber(rows).values()].map((group) => group.filter((each) => rowKey(each) !== key)))
}

/** The groups after the group of this number is taken apart. */
export const split = (rows: readonly QueueRow[], number: number): SameGroups =>
  asValue([...byNumber(rows)].filter(([each]) => each !== number).map(([, group]) => group))

/** How many rows stand in the group of this number. */
export const sizeOf = (rows: readonly QueueRow[], number: number): number => rows.filter((row) => row.same === number).length

/** The queue as it is tried: of the rows of one group the one the file holds comes first (of equals what it holds stays); the
 *  groups and the other rows keep the places the core gave them. The very list when nothing moves: a list of the same rows is
 *  not a new queue to the list that holds the order the user dragged until the core answers. */
export function heldFirst(rows: QueueRow[], held: Held | null | undefined): QueueRow[] {
  if (!held) return rows
  const first = rows.findIndex((row) => row.same != null && isVariantOf(held, row))
  if (first < 0) return rows
  const number = rows[first].same
  const at = rows.findIndex((row) => row.same === number)
  if (at === first) return rows
  const members = rows.filter((row) => row.same === number && row !== rows[first])
  const out = rows.filter((row) => row.same !== number)
  out.splice(at, 0, rows[first], ...members)
  return out
}
