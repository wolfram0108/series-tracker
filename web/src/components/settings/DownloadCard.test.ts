// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { mount } from "@vue/test-utils"
import DownloadCard from "./DownloadCard.vue"
import type { DownloadCard as Card } from "../../stores/queues"
import { t } from "../../i18n"

const cvh = { source_id: 55, source_n: 1, source_color: 0, place: "old.yummyani.me", player: "cvh", player_label: "CVH", height: 1080 }
const kodik = { ...cvh, player: "kodik", player_label: "Kodik", height: 720 }
const stages = (states: string[]) => ["queue", "link", "download", "remux", "check"].map((name, index) =>
  ({ name, state: states[index], seconds: 41 * (index + 1) })) as Card["stages"]

const card = (over: Partial<Card> = {}): Card => ({
  id: "88:s06e08:dub:Kazoku Project",
  need: { key: "88:s06e08:dub:Kazoku Project", series_id: 88, series_name: "ДжоДжо", sync_started: "2026-11-10T09:17:00+00:00",
          episode: "s06e08", kind: "dub", group: "Kazoku Project", voice_number: 15, cap: 720 },
  variant: kodik, status: "download", method: "ranged",
  stages: stages(["done", "done", "active", "skipped", "pending"]),
  log: [
    { kind: "attempt", variant: cvh, attempt: 3, of: 3, outcome: "failed", stage: "link", seconds: 126 },
    { kind: "replan", variant: kodik, attempt: 0, of: 0, outcome: "running", stage: null, seconds: 0 },
    { kind: "attempt", variant: kodik, attempt: 1, of: 3, outcome: "running", stage: null, seconds: 31 },
  ],
  figures: { progress: 47, done_mb: 312, total_mb: 664, speed: 10.4, eta: 34, source_seconds: 1452 },
  replanned: true, position: null, running: 0, limit: 0, queued_at: "2026-11-10T09:18:58+00:00", finished_at: "", seconds: 210,
  ...over,
} as Card)

describe("the card of a download", () => {
  it("names the need in the marks of «Выход», tells the stages, folds the attempts of a variant and shows the plan rebuilt", () => {
    const w = mount(DownloadCard, { props: { card: card() } })
    const text = w.text()
    expect(w.classes()).toContain("is-running")
    expect(w.findAll(".figure-mark").map((each) => each.text())).toEqual(["15", "1", "1", "1", "1"])
    expect(text).toContain("Kazoku Project")
    expect(text).toContain(t("download.attempt", { n: 3, of: 3 }))
    expect(text).toContain(t("download.failed.link"))
    expect(text).toContain(t("download.replan"))
    expect(w.findAll(".download-step").map((each) => each.classes().find((name) => name.startsWith("is-"))))
      .toEqual(["is-done", "is-done", "is-active", "is-skipped", "is-pending"])
    expect(w.find(".progress-bar").text()).toBe("47%")
    expect(text).toContain(t("download.method.ranged"))
    expect(w.find(".download-remove").exists()).toBe(false)
  })

  it("a failed card says that no variant is left and can be removed; a finished one is green", async () => {
    const failed = mount(DownloadCard, { props: { card: card({ status: "failed", log: [
      { kind: "attempt", variant: cvh, attempt: 3, of: 3, outcome: "failed", stage: "link", seconds: 126 },
      { kind: "exhausted", variant: null, attempt: 0, of: 0, outcome: "running", stage: null, seconds: 0 }],
      finished_at: "2026-11-10T09:21:15+00:00" }) } })
    expect(failed.classes()).toContain("is-failed")
    expect(failed.text()).toContain(t("download.exhausted"))
    expect(failed.text()).toContain(t("download.outcome.dub"))
    await failed.find(".download-remove").trigger("click")
    expect(failed.emitted("remove")).toEqual([["88:s06e08:dub:Kazoku Project"]])
    const done = mount(DownloadCard, { props: { card: card({ status: "done", finished_at: "2026-11-10T09:22:25+00:00" }) } })
    expect(done.classes()).toContain("is-done")
    expect(done.text()).toContain(t("download.label.got"))
  })
})
