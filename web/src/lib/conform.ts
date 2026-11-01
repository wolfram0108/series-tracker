import { t } from "../i18n"
import type { ConformResult, ConformTask } from "../stores/queues"

const ACTIVE = ["queued", "paused", "running"]

/** A job that waits or runs is stopped; one that ended is deleted from the history. */
export const isActive = (status: string): boolean => ACTIVE.includes(status)

/** The title of a card and the tag of its episode, from the label the service holds ("<series> sNNeNN"). */
export function splitTitle(label: string): { title: string; episode: string } {
  const found = /^(.*?)\s+s(\d+)e(\d+)$/i.exec(label)
  if (!found) return { title: label, episode: "" }
  return { title: found[1], episode: `S${found[2].padStart(2, "0")}E${found[3].padStart(2, "0")}` }
}

/** The service's own verdict, as the voices module reads it: `ok`. Its `suspect` and `critical` tell what it found and put right. */
export const isAccepted = (result: ConformResult): boolean => result.ok

const number = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 1 })

export function resultMetrics(result: ConformResult): string {
  if (result.skipped) return t("agents.conform.result.skipped")
  return t("agents.conform.result.ok", {
    resid: result.resid_ms == null ? "—" : number.format(result.resid_ms),
    coverage: result.coverage == null ? "—" : Math.round(result.coverage * 100),
  })
}

export function formatDuration(seconds: number): string {
  const total = Math.round(seconds)
  const min = Math.floor(total / 60)
  const sec = total % 60
  return min > 0
    ? t("agents.conform.duration.minSec", { min, sec: String(sec).padStart(2, "0") })
    : t("agents.conform.duration.sec", { sec })
}

export function footText(task: ConformTask): string {
  if (isActive(task.status) && task.status !== "running" && task.created_at) {
    const time = new Date(task.created_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })
    return t("agents.conform.added", { time })
  }
  return t("agents.conform.elapsed", { time: formatDuration(task.elapsed_s || 0) })
}

export type DubState = "result" | "running" | "waiting" | "cancelled"
export interface DubRow {
  file: string
  name: string
  state: DubState
  result: ConformResult | null
}

/** One row per voice-over of the job: its result when there is one, else what it is doing now. A failed job shows
 *  only the voice-overs that have a result: the rest of them never ran. */
export function dubRows(task: ConformTask, nameOf: (file: string) => string): DubRow[] {
  const files = task.dubs.length ? task.dubs : task.results.map((result) => result.dub)
  const rows: DubRow[] = []
  for (const file of files) {
    const result = task.results.find((each) => each.dub === file) ?? null
    let state: DubState = "waiting"
    if (result) state = "result"
    else if (task.status === "running" && file === task.cur_dub) state = "running"
    else if (task.status === "cancelled") state = "cancelled"
    else if (task.status === "failed") continue
    rows.push({ file, name: nameOf(file), state, result })
  }
  return rows
}
