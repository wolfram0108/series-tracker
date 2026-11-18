// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { mount } from "@vue/test-utils"
import DayLine from "../components/settings/DayLine.vue"
import FreshMark from "../components/FreshMark.vue"
import { t } from "../i18n"
import { freshState, shortDate } from "./freshness"
import { freeSpot, hhmm, minuteAt, minuteOf, scheduleOf, tidy } from "./schedule"

describe("the schedule of the day", () => {
  it("reads and writes a time of day and keeps a list sorted with no repeat", () => {
    expect([minuteOf("10:05"), minuteOf("9:00"), minuteOf("24:00"), minuteOf("x")]).toEqual([605, 540, null, null])
    expect([hhmm(605), hhmm(-5), hhmm(1440)]).toEqual(["10:05", "23:55", "00:00"])
    expect(tidy(["20:00", "10:00", "10:00", "bad"])).toEqual(["10:00", "20:00"])
  })

  it("puts a new point in the middle of the widest gap round the day, noon on an empty day", () => {
    expect(freeSpot([])).toBe("12:00")
    expect(freeSpot(["10:00", "16:00", "20:00"])).toBe("03:00")
    expect(freeSpot(["06:00"])).toBe("18:00")
  })

  it("names the minute of a place on the line, stepped and within the day", () => {
    expect([minuteAt(0), minuteAt(0.5), minuteAt(0.4167), minuteAt(1.2)]).toEqual([0, 720, 600, 1435])
  })

  it("takes the schedule the core sent with every list it may leave out", () => {
    expect(scheduleOf({ torrent_mode: "free", torrent_between: 2 })).toEqual(
      { syncs: [], torrent_mode: "free", torrent_free: [], torrent_between: 2 })
  })
})

describe("the line of the day", () => {
  it("shows every point at its time and moves a sync by a key, a check between the syncs not", async () => {
    const w = mount(DayLine, { props: { syncs: ["10:00", "16:00"], checks: ["13:00"], checksMove: false, picked: null } })
    const points = w.findAll(".day-point")
    expect(points.map((each) => each.attributes("aria-label"))).toEqual([
      t("schedule.syncPoint", { at: "10:00" }), t("schedule.syncPoint", { at: "16:00" }), t("schedule.checkPoint", { at: "13:00" })])
    expect(points[0].attributes("style")).toContain("41.66")
    await points[0].trigger("keydown", { key: "ArrowRight" })
    await points[2].trigger("keydown", { key: "ArrowRight" })
    expect(w.emitted("move")).toEqual([["sync", 0, "10:05"]])
    expect(w.find(".day-remove").exists()).toBe(false)
  })

  it("lets a free check picked be taken away", async () => {
    const w = mount(DayLine, { props: { syncs: [], checks: ["22:00"], checksMove: true, picked: { kind: "check", at: 0 } } })
    await w.find(".day-remove").trigger("click")
    expect(w.emitted("remove")).toEqual([["check", 0]])
  })
})

describe("how current the data of a source is", () => {
  const read = "2020-11-13T08:14:00Z"
  const until = "2020-11-14T08:14:00Z"

  it("is fresh within its term, stale past it and never for a release never checked", () => {
    expect(freshState(read, until, Date.parse(until) - 1)).toBe("fresh")
    expect(freshState(read, until, Date.parse(until))).toBe("stale")
    expect(freshState(read, null, Date.parse(until) + 1e9)).toBe("fresh")
    expect(freshState(null, null, 0)).toBe("never")
  })

  it("shows a dot with the time it came from, or the time it went stale", () => {
    const fresh = mount(FreshMark, { props: { at: read, until: "2999-01-01T00:00:00Z" } })
    expect(fresh.attributes("data-fresh")).toBe("fresh")
    expect(fresh.text()).toBe(t("fresh.from", { at: shortDate(read) }))
    const stale = mount(FreshMark, { props: { at: read, until } })
    expect(stale.attributes("data-fresh")).toBe("stale")
    expect(stale.text()).toBe(t("fresh.stale", { at: shortDate(until) }))
    expect(mount(FreshMark, { props: { at: null, until: null } }).text()).toBe(t("fresh.never"))
  })
})
