import type { PatternBlock } from "./patternBlocks"

/** A row of a rule «Таблица»: the template of «ЕСЛИ» and what «ТО» puts for the action of the column — a value for an
 *  assignment, a template for an extraction. */
export interface TableRow {
  key: number
  ifBlocks: PatternBlock[]
  thenValue: string
  thenBlocks: PatternBlock[]
}

/** The table as the core keeps it (`rule_table` of a rule): the action of the column and the rows in their order. */
export interface StoredTable {
  action_type: string
  rows: { if: Omit<PatternBlock, "id">[]; then: string | Omit<PatternBlock, "id">[] | null }[]
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
  return { key: next, ifBlocks: [], thenValue: "", thenBlocks: [] }
}

/** The row a quick template makes of one value: «ЕСЛИ» the text `code:value`, «ТО» the value itself. */
export function rowOf(code: string, value: string): TableRow {
  const row = emptyRow()
  row.ifBlocks = withIds([{ type: "text", value: `${code}:${value}` }])
  row.thenValue = value
  return row
}

/** Every text the rows of «ЕСЛИ» hold as a text block: a value a template would add again is one of them already, also
 *  in a row written by hand (`V:HORIZON` or `mir.donghua`). */
export function textsOf(rows: TableRow[]): Set<string> {
  return new Set(rows.flatMap((row) => row.ifBlocks.filter((block) => block.type === "text").map((block) => block.value ?? "")))
}

/** The rows of a stored table, ready to edit. */
export function rowsOf(table: StoredTable): TableRow[] {
  return table.rows.map((stored) => {
    const row = emptyRow()
    row.ifBlocks = withIds(stored.if ?? [])
    if (typeof stored.then === "string") row.thenValue = stored.then
    else if (Array.isArray(stored.then)) row.thenBlocks = withIds(stored.then)
    return row
  })
}

/** The table as the core takes it: what «ТО» holds follows the action of the column. */
export function storedOf(actionType: string, rows: TableRow[], extract: boolean): StoredTable {
  return {
    action_type: actionType,
    rows: rows.map((row) => ({
      if: withoutIds(row.ifBlocks),
      then: actionType === "exclude" ? null : extract ? withoutIds(row.thenBlocks) : row.thenValue.trim(),
    })),
  }
}
