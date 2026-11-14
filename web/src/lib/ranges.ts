// The one writing of the runs of episode numbers the window shows — `1–50, 52`, as the core words them
// (modules/composition/project.py, `spans`) — and what is read back out of it.

/** A run of episode numbers, both ends in it. */
export type Run = readonly [from: number, to: number]

/** `3–7`, or `4` for a run of one. */
export const rangeText = (from: number, to: number): string => (from === to ? `${from}` : `${from}–${to}`)

/** The runs of a set of numbers, ascending: a number right after another joins its run. */
export function runsOfNumbers(numbers: Iterable<number>): Run[] {
  const runs: [number, number][] = []
  for (const number of [...new Set(numbers)].sort((a, b) => a - b)) {
    const last = runs[runs.length - 1]
    if (last && number === last[1] + 1) last[1] = number
    else runs.push([number, number])
  }
  return runs
}

/** `1–50, 52` of a set of episode numbers, as the core words the episodes of a voice-over. */
export function spansOf(numbers: Iterable<number>): string {
  return runsOfNumbers(numbers).map(([from, to]) => rangeText(from, to)).join(", ")
}

/** The runs of the wording `1–50, 52`, ascending and joined where they touch; null when a piece of it is no run (a wording
 *  this window does not know is not guessed at). */
export function runsOfText(text: string): Run[] | null {
  const found: [number, number][] = []
  for (const piece of text.split(",").map((each) => each.trim()).filter((each) => each !== "")) {
    const match = /^(\d+)(?:–(\d+))?$/.exec(piece)
    if (!match) return null
    const from = Number(match[1])
    found.push([from, match[2] === undefined ? from : Number(match[2])])
  }
  const runs: [number, number][] = []
  for (const [from, to] of found.sort((a, b) => a[0] - b[0])) {
    const last = runs[runs.length - 1]
    if (last && from <= last[1] + 1) last[1] = Math.max(last[1], to)
    else runs.push([from, to])
  }
  return runs
}

/** The numbers missing between ascending runs, as runs. */
export function gapsBetween(runs: readonly Run[]): Run[] {
  return runs.slice(1).map((run, index): Run => [runs[index][1] + 1, run[0] - 1])
}

/** What the episodes of a voice-over come to: from the first to the last, and the gaps between them as sources write them.
 *  A wording that is no runs stands as it is, whole: nothing is hidden for not being understood. */
export interface Reach { span: string; gaps: string }

export function reachOf(text: string): Reach {
  const runs = runsOfText(text)
  if (runs === null || runs.length === 0) return { span: text, gaps: "" }
  return {
    span: rangeText(runs[0][0], runs[runs.length - 1][1]),
    gaps: gapsBetween(runs).map(([from, to]) => rangeText(from, to)).join(", "),
  }
}
