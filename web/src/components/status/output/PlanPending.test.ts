// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest"
import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import PlanPending from "./PlanPending.vue"
import * as calls from "../../../composables/outputCalls"
import { usePlanStore } from "../../../stores/plan"
import { t } from "../../../i18n"
import { failure as attempt, planView, rowRef } from "../../../lib/plan.fixtures"

vi.mock("../../../composables/outputCalls")

const failure = (key: string, covered: boolean) => attempt({ kind: "align", row: rowRef(90, key), episodes: [[1, 498]], voice: "ПВА ШОУ", covered })

describe("what the plan holds for the next sync, as chips", () => {
  beforeEach(() => setActivePinia(createPinia()))

  const mountWith = (over: Parameters<typeof planView>[0]) => {
    usePlanStore().apply(planView(over))
    return mount(PlanPending, { props: { seriesId: 1 } })
  }

  it("counts the failed attempts and, of them, the ones nothing replaced: those lack something to the series", () => {
    const w = mountWith({ failures: [failure("a", true), failure("b", true), failure("c", false)] })
    expect(w.find(".plan-failures").text()).toBe(t("output.plan.failuresOpen", { count: 3, open: 1 }))
    expect(w.find(".plan-failures").classes()).toContain("pill-danger")
  })

  it("is a quiet chip, with no word of what lacks, when another source gave what every failed attempt was for", () => {
    const w = mountWith({ failures: [failure("a", true), failure("b", true)] })
    expect(w.find(".plan-failures").text()).toBe(t("output.plan.failures", { count: 2 }))
    expect(w.find(".plan-failures").classes()).toContain("pill-secondary")
    expect(w.find(".plan-failures").attributes("title")).toBe(t("output.plan.failuresHint"))
  })

  it("has a chip for each command «Удалить» that waits, with its own way back, and nothing for a plan that holds nothing", async () => {
    const w = mountWith({ commands: [{ kind: "video", name: "S01E01.mkv", dub: null }] })
    expect(w.find(".plan-command").text()).toBe(t("output.plan.deleteVideo", { name: "S01E01.mkv" }))
    await w.find(".plan-command button").trigger("click")
    expect(vi.mocked(calls.cancelDelete)).toHaveBeenCalledWith(1, { kind: "video", name: "S01E01.mkv", dub: null })
    expect(mountWith({}).find(".plan-pending").exists()).toBe(false)
  })
})
