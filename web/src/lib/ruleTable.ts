import type { PatternBlock } from "./patternBlocks"
import { isExtract } from "./ruleVocabulary"

/** The cells of a row. A cell is an object of its own: rows whose cells are joined («объединить ячейки») hold the same
 *  one, so what is typed into it is typed for each of them. */
export interface IfCell { blocks: PatternBlock[] }
/** `action`: the action of the cell when it is not the column's (a row of a table may do another thing). */
export interface ThenCell { value: string; blocks: PatternBlock[]; action?: string }
export type Side = "if" | "then"

/** A row of a rule «Таблица»: the template of «ЕСЛИ» and what «ТО» puts for the action of the column — a value for an
 *  assignment, a template for an extraction. A cell joined with the one above is the same cell: one «ЕСЛИ» with several
 *  «ТО» — for the core one rule with those actions in order (of one kind alternatives, the first that works wins; of
 *  different kinds each acts: the episode and the season) — or several «ЕСЛИ» with one «ТО», each row a rule of its own. */
export interface TableRow {
  key: number
  if: IfCell
  then: ThenCell
}

type StoredBlocks = Omit<PatternBlock, "id">[]

/** The table as the core keeps it (`rule_table` of a rule): the action of the column and the rows in their order, each
 *  with both its cells whole; `if_joined` / `then_joined` say the cell is the one of the row above (for the editor). */
export interface StoredTable {
  action_type: string
  /** A table of folders: the language of every row that names none of its own. */
  language?: string
  rows: { if: StoredBlocks; then: string | number | StoredBlocks | null; if_joined?: boolean; then_joined?: boolean
    /** The action of the row when it is not the column's. */
    action_type?: string }[]
}

const SEPARATOR = " | "

/** Every distinct value of a coded field (`V`, `PID`, ...) in the lines of the test, in the order it first appears. */
export function fieldValues(lines: string[], code: string): string[] {
  const prefix = `${code}:`
  const seen = new Set<string>()
  for (const line of lines) {
    for (const part of line.split(SEPARATOR)) {
      const text = part.trim()
      if (text.startsWith(prefix) && text.length > prefix.length) seen.add(text.slice(prefix.length).trim())
    }
  }
  return [...seen].filter((value) => value !== "")
}

let next = 0
const withIds = (blocks: Omit<PatternBlock, "id">[]): PatternBlock[] =>
  blocks.map((block) => ({ ...block, id: Date.now() + Math.random() }))
const withoutIds = (blocks: PatternBlock[]): Omit<PatternBlock, "id">[] => blocks.map(({ id: _id, ...rest }) => rest)

export function emptyRow(): TableRow {
  next += 1
  return { key: next, if: { blocks: [] }, then: { value: "", blocks: [] } }
}

/** The row a quick template makes of one value: «ЕСЛИ» the text `code:value`, «ТО» the value itself. */
export function rowOf(code: string, value: string): TableRow {
  const row = emptyRow()
  row.if.blocks = withIds([{ type: "text", value: `${code}:${value}` }])
  row.then.value = value
  return row
}

/** Every text the rows of «ЕСЛИ» hold as a text block: a value a template would add again is one of them already, also
 *  in a row written by hand (`V:HORIZON` or `mir.donghua`). */
export function textsOf(rows: TableRow[]): Set<string> {
  return new Set(rows.flatMap((row) => row.if.blocks.filter((block) => block.type === "text").map((block) => block.value ?? "")))
}

/** Whether the cell of the row is the one of the row above. */
export const joined = (rows: TableRow[], at: number, side: Side): boolean => at > 0 && rows[at][side] === rows[at - 1][side]

/** The rows the cell that covers the row spans: [the first, the one after the last]. */
export function spanOf(rows: TableRow[], at: number, side: Side): [number, number] {
  let first = at
  while (joined(rows, first, side)) first -= 1
  let end = first + 1
  while (end < rows.length && joined(rows, end, side)) end += 1
  return [first, end]
}

/** Whether the cell that covers the row can be joined with the cell below it: there is one, and the two are not the
 *  same rows on the other side already (rows alike in both cells would be one rule written twice). */
export function canJoin(rows: TableRow[], at: number, side: Side): boolean {
  const [, end] = spanOf(rows, at, side)
  return end < rows.length && !joined(rows, end, side === "if" ? "then" : "if")
}

/** The cell that covers the row joined with the cell below it: the rows of that one take this cell (what the lower cell
 *  held is dropped, as in any table). */
export function join(rows: TableRow[], at: number, side: Side): void {
  if (!canJoin(rows, at, side)) return
  const [first, end] = spanOf(rows, at, side)
  const [, below] = spanOf(rows, end, side)
  for (let each = end; each < below; each++) (rows[each][side] as IfCell | ThenCell) = rows[first][side]
}

const copyOf = <Cell extends IfCell | ThenCell>(cell: Cell): Cell =>
  ({ ...cell, blocks: withIds(withoutIds(cell.blocks)) })

/** The cell that covers the row made the cells of its rows again, each with what the joined one held. */
export function split(rows: TableRow[], at: number, side: Side): void {
  const [first, end] = spanOf(rows, at, side)
  for (let each = first + 1; each < end; each++) (rows[each][side] as IfCell | ThenCell) = copyOf(rows[each][side])
}

const other = (side: Side): Side => (side === "if" ? "then" : "if")

/** The rows of the cells of a column from the one that covers row `a` to the one that covers row `b`, whole cells:
 *  [the first row, the one after the last]. */
export function rangeOf(rows: TableRow[], a: number, b: number, side: Side): [number, number] {
  const [low, high] = a <= b ? [a, b] : [b, a]
  return [spanOf(rows, low, side)[0], spanOf(rows, high, side)[1]]
}

/** How many cells of the column the rows hold. */
export function cellsIn(rows: TableRow[], first: number, end: number, side: Side): number {
  let cells = 0
  for (let at = first; at < end; at++) if (at === first || !joined(rows, at, side)) cells += 1
  return cells
}

/** Whether the cells of the rows can be made one: there are several, and no two of them are one cell on the other side
 *  already (rows alike in both cells would be one rule written twice). */
export function canJoinRange(rows: TableRow[], first: number, end: number, side: Side): boolean {
  if (cellsIn(rows, first, end, side) < 2) return false
  for (let at = first + 1; at < end; at++) if (!joined(rows, at, side) && joined(rows, at, other(side))) return false
  return true
}

/** The cells of the rows made one cell: it holds what the uppermost held, what the others held is dropped. */
export function joinRange(rows: TableRow[], first: number, end: number, side: Side): void {
  if (!canJoinRange(rows, first, end, side)) return
  for (let at = first + 1; at < end; at++) (rows[at][side] as IfCell | ThenCell) = rows[first][side]
}

/** Every joined cell of the rows made the cells of its rows again, each with what the joined one held. */
export function splitRange(rows: TableRow[], first: number, end: number, side: Side): void {
  for (let at = end - 1; at > first; at--) if (joined(rows, at, side)) (rows[at][side] as IfCell | ThenCell) = copyOf(rows[at][side])
}

/** After rows were moved, added or removed: a row taken out of the rows it shared a cell with gets a cell of its own
 *  with the same content, so a cell is always a run of neighbouring rows. */
export function settle(rows: TableRow[]): void {
  for (const side of ["if", "then"] as const) {
    const closed = new Set<IfCell | ThenCell>()
    rows.forEach((row, at) => {
      if (joined(rows, at, side)) return
      if (closed.has(row[side])) (row[side] as IfCell | ThenCell) = copyOf(row[side])
      closed.add(row[side])
    })
  }
}

/** The rows of a stored table, ready to edit. */
export function rowsOf(table: StoredTable): TableRow[] {
  const rows: TableRow[] = []
  for (const stored of table.rows) {
    const row = emptyRow()
    const above = rows[rows.length - 1]
    if (stored.if_joined && above) row.if = above.if
    else row.if.blocks = withIds(stored.if ?? [])
    if (stored.then_joined && above) row.then = above.then
    // A value is text; a table stored with a number (the core takes both) is read as its text.
    else if (typeof stored.then === "string" || typeof stored.then === "number") row.then.value = String(stored.then)
    else if (Array.isArray(stored.then)) row.then.blocks = withIds(stored.then)
    if (!(stored.then_joined && above) && stored.action_type && stored.action_type !== table.action_type) row.then.action = stored.action_type
    rows.push(row)
  }
  return rows
}

/** The table as the core takes it: what «ТО» holds follows the action of the column. */
export function storedOf(actionType: string, rows: TableRow[], language = ""): StoredTable {
  return {
    action_type: actionType,
    ...(language && { language }),
    rows: rows.map((row, at) => {
      // The value is written for the action of the row: its own, else the column's.
      const own = row.then.action && row.then.action !== actionType ? row.then.action : ""
      const type = own || actionType
      return {
        if: withoutIds(row.if.blocks),
        then: type === "exclude" ? null : isExtract(type) ? withoutIds(row.then.blocks) : row.then.value.trim(),
        ...(joined(rows, at, "if") && { if_joined: true }),
        ...(joined(rows, at, "then") && { then_joined: true }),
        ...(own && { action_type: own }),
      }
    }),
  }
}
