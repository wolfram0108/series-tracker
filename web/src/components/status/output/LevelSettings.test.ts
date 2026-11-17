// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest"
import { mount } from "@vue/test-utils"
import LevelSettings from "./LevelSettings.vue"

// jsdom lays nothing out: the window's measuring of its header has nothing to observe.
vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} })

describe("the settings of a level", () => {
  it("has its tabs in a strip of the body that wraps, as many as its voice-overs, not in the header of the window", () => {
    const order = Array.from({ length: 28 }, (_, index) => `Озвучка ${index + 1}`)
    const w = mount(LevelSettings, { props: { season: 6, order, marks: {} }, attachTo: document.body })
    const strip = document.body.querySelector(".level-settings .st-tabs-wrap")
    // tracks, «РЕФ», «Эталон», every voice-over and the subtitles of the season
    expect(strip?.querySelectorAll(".st-tab").length).toBe(3 + 28 + 1)
    expect(strip?.querySelector(".st-tab.tab-subtitles")).not.toBeNull()
    expect(document.body.querySelector(".modern-header .st-tabs")).toBeNull()
    w.unmount()
  })
})
