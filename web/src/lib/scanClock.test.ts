import { describe, expect, it } from "vitest"
import { t } from "../i18n"
import { scanClock } from "./scanClock"

const NEXT = "2026-11-18T10:40:00+00:00"
const at = Date.parse(NEXT)
const status = (over = {}) => ({ scanner_enabled: true, is_scanning: false, next_scan_time: NEXT, schedule: { torrent_mode: "between" as const, torrent_between: 1 }, ...over })

describe("the clock of the scheduled scan", () => {
  it("says in how long, in whole minutes rounded up, and when its words change next", () => {
    const later = scanClock(status(), at - (3 * 60 + 11) * 60_000 - 30_000)!
    expect(later.text).toBe(t("header.scan.in", { time: t("header.scan.hoursMinutes", { h: 3, m: 12 }) }))
    expect(later.changesIn).toBe(30_000)
    expect(scanClock(status(), at - 59_000)!.text).toBe(t("header.scan.in", { time: t("header.scan.minutes", { m: 1 }) }))
    expect(scanClock(status(), at - 59_000)!.changesIn).toBe(59_000)
  })

  it("says the scan is starting once its time came, and going while it goes", () => {
    expect(scanClock(status(), at + 5_000)).toMatchObject({ text: t("header.scan.due"), changesIn: null })
    expect(scanClock(status({ is_scanning: true }), at - 60_000)).toMatchObject({ text: t("header.scan.running"), changesIn: null })
  })

  it("says nothing while the scanner is off or has no time", () => {
    expect(scanClock(null, at)).toBeNull()
    expect(scanClock(status({ scanner_enabled: false }), at)).toBeNull()
    expect(scanClock(status({ next_scan_time: null }), at)).toBeNull()
  })
})
