import type { components } from "../api/schema"

export type ScheduleValue = Required<components["schemas"]["ScheduleValue"]>
export type PointKind = "sync" | "check"

export const DAY = 24 * 60
/** A point dragged or moved by a key steps by this many minutes; the field of «Точное время» takes any minute. */
export const STEP = 5

export const EMPTY_SCHEDULE: ScheduleValue = { syncs: [], torrent_mode: "between", torrent_free: [], torrent_between: 1 }

/** The schedule the core sent, with every list it may leave out. */
export const scheduleOf = (sent: components["schemas"]["ScheduleValue"] | undefined): ScheduleValue =>
  ({ ...EMPTY_SCHEDULE, ...sent, syncs: sent?.syncs ?? [], torrent_free: sent?.torrent_free ?? [] })

/** The minute of the day «HH:MM» names; null for anything else. */
export function minuteOf(text: string): number | null {
  const found = /^(\d{1,2}):(\d{2})$/.exec(text.trim())
  if (!found) return null
  const hour = Number(found[1])
  const minute = Number(found[2])
  return hour < 24 && minute < 60 ? hour * 60 + minute : null
}

export function hhmm(minute: number): string {
  const wrapped = ((minute % DAY) + DAY) % DAY
  return `${String(Math.floor(wrapped / 60)).padStart(2, "0")}:${String(wrapped % 60).padStart(2, "0")}`
}

/** The times of a list, sorted and with no repeat: what the core keeps of it. */
export function tidy(times: readonly string[]): string[] {
  return [...new Set(times.map(minuteOf).filter((each): each is number => each !== null))].sort((a, b) => a - b).map(hhmm)
}

/** Where a new point goes: the middle of the widest gap the points leave round the day (noon on an empty day). */
export function freeSpot(taken: readonly string[]): string {
  const minutes = [...new Set(taken.map(minuteOf).filter((each): each is number => each !== null))].sort((a, b) => a - b)
  if (!minutes.length) return "12:00"
  let best = { start: minutes[0], gap: 0 }
  minutes.forEach((start, at) => {
    const gap = ((minutes[(at + 1) % minutes.length] - start + DAY) % DAY) || DAY
    if (gap > best.gap) best = { start, gap }
  })
  return hhmm(best.start + Math.round(best.gap / 2 / STEP) * STEP)
}

/** The minute of the day a place on the line names, as a share of its width, stepped. */
export function minuteAt(share: number): number {
  const minute = Math.round((Math.min(Math.max(share, 0), 1) * DAY) / STEP) * STEP
  return Math.min(minute, DAY - STEP)
}

/** The place of a time on the line, a share of its width in percent. */
export const placeOf = (time: string): number => ((minuteOf(time) ?? 0) / DAY) * 100
