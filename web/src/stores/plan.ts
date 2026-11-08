import { defineStore } from "pinia"
import { shallowRef } from "vue"
import type { PlannerView } from "../lib/plan"

/** The plan of every video series the window has open, fed by the core's pushes (plan_update) and by one read when a
 *  window opens. A packet is the whole plan of its series: it replaces what was there. */
export const usePlanStore = defineStore("plan", () => {
  const bySeries = shallowRef<Record<number, PlannerView>>({})

  function apply(view: PlannerView): void {
    bySeries.value = { ...bySeries.value, [view.series_id]: view }
  }

  const forSeries = (seriesId: number): PlannerView | undefined => bySeries.value[seriesId]

  return { bySeries, apply, forSeries }
})
