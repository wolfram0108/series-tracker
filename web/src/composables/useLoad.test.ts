// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest"
import { nextTick } from "vue"
import { mount } from "@vue/test-utils"

import { useLoad } from "./useLoad"
import { useAction } from "./useAction"
import LoadGate from "../components/LoadGate.vue"

const later = <T>() => {
  let finish!: (value: T) => void
  const promise = new Promise<T>((resolve) => { finish = resolve })
  return { promise, finish }
}

describe("the read a screen waits for", () => {
  it("is loading until it is read, then loaded; a read that could not be is failed, and read again it is loaded", async () => {
    const answers = [false, true]
    const load = useLoad(async () => answers.shift()!)
    expect(load.state.value).toBe("idle")
    const first = load.run()
    expect(load.state.value).toBe("loading")
    await first
    expect(load.state.value).toBe("failed")
    await load.run()
    expect(load.state.value).toBe("loaded")
  })

  it("takes a read that throws for a read that failed", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {})
    const load = useLoad(async () => { throw new Error("away") })
    await load.run()
    expect(load.state.value).toBe("failed")
  })

  it("shows the skeleton for the first read only: a read that follows keeps what is on show, also where it fails", async () => {
    const wait = later<boolean>()
    const answers = [Promise.resolve(true), wait.promise]
    const load = useLoad(() => answers.shift()!)
    await load.run()
    const again = load.run()
    expect(load.state.value).toBe("loaded") // not the skeleton again
    wait.finish(false)
    await again
    expect(load.state.value).toBe("loaded") // not the failure of a screen that has its data
  })

  it("reads once for the asks that come while it goes on", async () => {
    const wait = later<boolean>()
    const read = vi.fn(() => wait.promise)
    const load = useLoad(read)
    const a = load.run()
    const b = load.run()
    wait.finish(true)
    await Promise.all([a, b])
    expect(read).toHaveBeenCalledOnce()
  })
})

describe("the gate of a screen", () => {
  const gate = (state: "loading" | "loaded" | "failed") => mount(LoadGate, { props: { state }, slots: { default: '<p class="form">form</p>' } })

  it("builds the form only where it has been read: the skeleton, named for the reader of the screen, stands in its place", () => {
    const loading = gate("loading")
    expect(loading.find(".form").exists()).toBe(false)
    expect(loading.find(".load-skeleton").attributes("aria-busy")).toBe("true")
    expect(loading.findAll(".load-field").length).toBeGreaterThan(0)
    expect(gate("loaded").find(".form").exists()).toBe(true)
  })

  it("says it could not be read and gives a button for another try", async () => {
    const failed = gate("failed")
    expect(failed.find(".form").exists()).toBe(false)
    expect(failed.find("[role=alert]").exists()).toBe(true)
    await failed.find("button").trigger("click")
    expect(failed.emitted("retry")).toHaveLength(1)
  })
})

describe("an action pressed more than once", () => {
  it("is done once while the first press is not answered, and again after", async () => {
    const wait = later<void>()
    const act = vi.fn(() => wait.promise)
    const action = useAction(act)
    void action.run()
    void action.run()
    expect(act).toHaveBeenCalledOnce()
    expect(action.pending()).toBe(true)
    wait.finish()
    await nextTick()
    await nextTick()
    expect(action.pending()).toBe(false)
    await action.run()
    expect(act).toHaveBeenCalledTimes(2)
  })

  it("is one per thing it is done to: the sync of a series does not hold up the sync of another", () => {
    const action = useAction((_id: number) => new Promise<void>(() => {}), (id: number) => String(id))
    void action.run(1)
    void action.run(1)
    void action.run(2)
    expect(action.pending("1")).toBe(true)
    expect(action.pending("2")).toBe(true)
    expect(action.pending("3")).toBe(false)
  })
})
