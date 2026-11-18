/** How current the data of a source is at `now`: `fresh` while its term holds (or nothing ends it), `stale` once the end
 *  the core named is past, `never` for data never got (a release never checked). */
export type FreshState = "fresh" | "stale" | "never"

export function freshState(at: string | null, until: string | null, now: number): FreshState {
  if (!at) return "never"
  return until && Date.parse(until) <= now ? "stale" : "fresh"
}

const SHORT = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })

/** «18.11, 12:19»: the day and the time a window shows a moment by. */
export function shortDate(iso: string): string {
  return SHORT.format(new Date(iso))
}
