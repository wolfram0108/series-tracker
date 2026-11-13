// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { mount, flushPromises, type VueWrapper } from "@vue/test-utils"
import PickDropdown from "./PickDropdown.vue"

let mounted: VueWrapper | null = null
afterEach(() => {
  mounted?.unmount()
  document.body.innerHTML = ""
})

const options = () => [...document.body.querySelectorAll(".pick-list .option")].map((each) => each.textContent)

describe("PickDropdown", () => {
  it("asks for its values when it opens, lists them, emits the one that is clicked and closes", async () => {
    const load = vi.fn().mockResolvedValue(undefined)
    mounted = mount(PickDropdown, {
      props: { items: [{ value: "a", label: "A" }, { value: "b", label: "B" }], title: "Pick", empty: "Nothing", load },
      attachTo: document.body,
    })
    expect(options()).toEqual([])
    await mounted.find(".pick-trigger").trigger("click")
    await flushPromises()
    expect(load).toHaveBeenCalledTimes(1)
    expect(options()).toEqual(["A", "B"])
    ;(document.body.querySelectorAll(".pick-list .option")[1] as HTMLElement).click()
    await flushPromises()
    expect(mounted.emitted("select")).toEqual([["b"]])
    expect(options()).toEqual([])
  })

  it("says so when it has nothing to offer, and closes on a click outside", async () => {
    mounted = mount(PickDropdown, { props: { items: [], title: "Pick", empty: "Nothing" }, attachTo: document.body })
    await mounted.find(".pick-trigger").trigger("click")
    await flushPromises()
    expect(document.body.querySelector(".pick-empty")?.textContent).toBe("Nothing")
    document.body.click()
    await flushPromises()
    expect(document.body.querySelector(".pick-list")).toBeNull()
  })
})
