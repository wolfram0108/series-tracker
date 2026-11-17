import { t } from "../i18n"
import type { ScannerStatus } from "../stores/scanner"

const MINUTE_MS = 60_000

/** What the header says of the scheduled scan at `now` (ms): going, starting, or in how long; nothing while the scanner is
 *  off or has no time set. `changesIn` is when the words change next (ms), null when only a new status changes them. */
export function scanClock(status: ScannerStatus | null, now: number): { text: string; at: string; changesIn: number | null } | null {
  if (!status) return null
  if (status.is_scanning) return { text: t("header.scan.running"), at: "", changesIn: null }
  if (!status.scanner_enabled || !status.next_scan_time) return null
  const next = Date.parse(status.next_scan_time)
  if (Number.isNaN(next)) return null
  const at = t("header.scan.at", { at: new Date(next).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) })
  const left = next - now
  if (left <= 0) return { text: t("header.scan.due"), at, changesIn: null }
  // Whole minutes rounded up: «1 мин» until the time itself comes.
  const minutes = Math.ceil(left / MINUTE_MS)
  const hours = Math.floor(minutes / 60)
  const time = hours > 0 ? t("header.scan.hoursMinutes", { h: hours, m: minutes % 60 }) : t("header.scan.minutes", { m: minutes })
  return { text: t("header.scan.in", { time }), at, changesIn: left - (minutes - 1) * MINUTE_MS }
}
