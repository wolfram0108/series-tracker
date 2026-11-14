// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { mount, type VueWrapper } from "@vue/test-utils"
import { defineComponent, h } from "vue"

vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} })  // the tabs fit their room (ModalShell)
Element.prototype.scrollIntoView = () => {}  // jsdom has no layout

import ModalShell from "./ModalShell.vue"
import StSelect from "./StSelect.vue"
import { useConfirm } from "../composables/useConfirm"

const mounted: VueWrapper[] = []
afterEach(() => {
  useConfirm().cancel()
  while (mounted.length) mounted.pop()!.unmount()
  document.body.innerHTML = ""
})

function open(props: Record<string, unknown> = {}, body = "") {
  const wrapper = mount(ModalShell, { attachTo: document.body, props: { title: "Window", ...props }, slots: { default: body } })
  mounted.push(wrapper)
  return wrapper
}
function press(key: string, init: KeyboardEventInit = {}, on: EventTarget = document): KeyboardEvent {
  const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init })
  on.dispatchEvent(event)
  return event
}

describe("the window", () => {
  it("is a dialog named by its title, and takes the focus when it opens", () => {
    const window = open()
    const dialog = window.find(".modern-modal")
    expect(dialog.attributes("role")).toBe("dialog")
    expect(dialog.attributes("aria-modal")).toBe("true")
    expect(window.find(`#${dialog.attributes("aria-labelledby")}`).text()).toBe("Window")
    expect(document.activeElement).toBe(dialog.element)
  })

  it("gives the focus back to what opened it", () => {
    const opener = document.createElement("button")
    document.body.append(opener)
    opener.focus()
    const window = open()
    expect(document.activeElement).not.toBe(opener)
    window.unmount()
    mounted.pop()
    expect(document.activeElement).toBe(opener)
  })

  it("keeps Tab inside: from the last element to the first, from the first back to the last, from outside to the first", () => {
    open({}, '<button id="b">B</button>')
    const first = document.querySelector<HTMLElement>(".modern-close")! // the first element of the window is its cross
    const last = document.getElementById("b")!
    last.focus()
    expect(press("Tab").defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(first)
    expect(press("Tab", { shiftKey: true }).defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(last)
    ;(document.activeElement as HTMLElement).blur()
    press("Tab")
    expect(document.activeElement).toBe(first)
    expect(press("Tab").defaultPrevented).toBe(false) // inside the ring the browser walks on its own
  })

  it("is closed by Escape on top alone: the window under it stays", () => {
    const under = open()
    const over = open()
    press("Escape")
    expect(over.emitted("close")).toHaveLength(1)
    expect(under.emitted("close")).toBeUndefined()
    over.unmount()
    mounted.pop()
    press("Escape")
    expect(under.emitted("close")).toHaveLength(1)
  })

  it("is not closed by an Escape that an element has taken for itself", () => {
    const window = open({}, '<input id="name" />')
    document.getElementById("name")!.addEventListener("keydown", (event) => event.preventDefault())
    press("Escape", {}, document.getElementById("name")!)
    expect(window.emitted("close")).toBeUndefined()
  })

  it("asks before it is closed over what was typed, and closes on the user's word", async () => {
    const window = open({ dirty: true })
    press("Escape")
    expect(useConfirm().state.visible).toBe(true)
    expect(window.emitted("close")).toBeUndefined()
    useConfirm().cancel()
    await Promise.resolve()
    expect(window.emitted("close")).toBeUndefined()

    press("Escape")
    useConfirm().confirm()
    await vi.waitFor(() => expect(window.emitted("close")).toHaveLength(1))
  })

  it("closes at once where nothing was typed", () => {
    const window = open({ dirty: false })
    press("Escape")
    expect(useConfirm().state.visible).toBe(false)
    expect(window.emitted("close")).toHaveLength(1)
  })
})

describe("the select in a window", () => {
  const options = [{ label: "A", value: 1 }, { label: "B", value: 2 }]
  const host = () => {
    const wrapper = mount(
      defineComponent({ emits: ["close", "pick"], setup: (_, { emit }) => () => h(ModalShell, { onClose: () => emit("close") }, {
        default: () => h(StSelect, { options, modelValue: 1, ariaLabel: "Pick", "onUpdate:modelValue": (value: unknown) => emit("pick", value) }),
      }) }),
      { attachTo: document.body },
    )
    mounted.push(wrapper)
    return wrapper
  }
  const trigger = () => document.querySelector<HTMLElement>("[role=combobox]")!

  it("is reached by Tab, named, and worked with the arrows and Enter", async () => {
    const wrapper = host()
    expect(trigger().tabIndex).toBe(0)
    expect(trigger().getAttribute("aria-label")).toBe("Pick")
    press("ArrowDown", {}, trigger())
    await vi.waitFor(() => expect(document.querySelector("[role=listbox]")).not.toBeNull())
    expect(trigger().getAttribute("aria-expanded")).toBe("true")
    press("ArrowDown", {}, trigger())
    press("Enter", {}, trigger())
    expect(wrapper.emitted("pick")).toEqual([[2]])
    await vi.waitFor(() => expect(document.querySelector("[role=listbox]")).toBeNull())
  })

  it("is closed by Escape before the window is", async () => {
    const wrapper = host()
    press("Enter", {}, trigger())
    await vi.waitFor(() => expect(document.querySelector("[role=listbox]")).not.toBeNull())
    press("Escape", {}, trigger())
    await vi.waitFor(() => expect(document.querySelector("[role=listbox]")).toBeNull())
    expect(wrapper.emitted("close")).toBeUndefined()
    press("Escape", {}, trigger())
    expect(wrapper.emitted("close")).toHaveLength(1)
  })
})
