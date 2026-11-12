/** The episodes selected on the tab «Выход», as in a file manager: a click takes one episode, Shift+click the run from
 *  the episode clicked before to this one in the order shown, Ctrl+click adds or removes one. A run lies within one
 *  season: its order is the one of that season's grid. */
export interface Selection {
  /** "season:episode" of every episode selected. */
  keys: ReadonlySet<string>
  /** Where the next run starts: the episode of the last plain click or Ctrl+click. */
  anchor: { season: number; episode: number } | null
}

export type Pick = "one" | "run" | "toggle"

export const NO_SELECTION: Selection = { keys: new Set(), anchor: null }

export const keyOf = (season: number, episode: number): string => `${season}:${episode}`

/** What a click asks of the selection, by the keys held while it was made. */
export function pickOf(event: Pick | { shiftKey: boolean; ctrlKey: boolean; metaKey: boolean }): Pick {
  if (typeof event === "string") return event
  return event.shiftKey ? "run" : event.ctrlKey || event.metaKey ? "toggle" : "one"
}

/** The selection after a click on an episode; `shown` is the order of the episodes of its season on the screen. */
export function picked(selection: Selection, season: number, episode: number, pick: Pick, shown: readonly number[]): Selection {
  const key = keyOf(season, episode)
  if (pick === "toggle") {
    const keys = new Set(selection.keys)
    if (keys.has(key)) keys.delete(key)
    else keys.add(key)
    return { keys, anchor: { season, episode } }
  }
  const anchor = selection.anchor
  const from = anchor && anchor.season === season ? shown.indexOf(anchor.episode) : -1
  const to = shown.indexOf(episode)
  if (pick === "run" && from >= 0 && to >= 0) {
    const [low, high] = from <= to ? [from, to] : [to, from]
    return { keys: new Set(shown.slice(low, high + 1).map((each) => keyOf(season, each))), anchor }
  }
  return { keys: new Set([key]), anchor: { season, episode } }
}

/** The episodes of a season the selection holds, in the order given. */
export function selectedOf(selection: Selection, season: number, episodes: readonly number[]): number[] {
  return episodes.filter((episode) => selection.keys.has(keyOf(season, episode)))
}

/** Which way one press switches the episodes selected: off when any of them is on, on when all are off — the selection
 *  comes out one way whatever it held. */
export function switchesOff(on: readonly boolean[]): boolean {
  return on.some(Boolean)
}
