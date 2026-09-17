// @vitest-environment jsdom
import { describe, it, expect } from "vitest"
import { mount, type VueWrapper } from "@vue/test-utils"
import StInput from "./StInput.vue"

async function type(w: VueWrapper, text: string, caret = text.length) {
  const input = w.find("input")
  input.element.value = text
  input.element.setSelectionRange(caret, caret)
  await input.trigger("input")
  return input.element
}

const last = (w: VueWrapper) => w.emitted("update:modelValue")?.at(-1)?.[0]

describe("StInput kind", () => {
  it("drops a forbidden character while typing and keeps the caret", async () => {
    const w = mount(StInput, { props: { label: "x", kind: "filename", modelValue: "Star Trek" } })
    const el = await type(w, "Star: Trek", 5)
    expect(el.value).toBe("Star Trek")
    expect(el.selectionStart).toBe(4)
    expect(last(w)).toBe("Star Trek")
  })

  it("keeps trailing whitespace in the field but trims the model", async () => {
    const w = mount(StInput, { props: { label: "x", kind: "filename", modelValue: "" } })
    const el = await type(w, "Star ")
    expect(el.value).toBe("Star ")
    expect(last(w)).toBe("Star")
  })

  it("turns backslashes into slashes for a path", async () => {
    const w = mount(StInput, { props: { label: "x", kind: "path", modelValue: "" } })
    const el = await type(w, "C:\\nas\\x")
    expect(el.value).toBe("C/nas/x")
    expect(last(w)).toBe("C/nas/x")
  })

  it("normalizes values written by code", async () => {
    const w = mount(StInput, { props: { label: "x", kind: "path", modelValue: "/nas/Show: One" } })
    expect(last(w)).toBe("/nas/Show One")
    await w.setProps({ modelValue: " /nas/y " })
    expect(last(w)).toBe("/nas/y")
  })

  it("leaves plain fields untouched", async () => {
    const w = mount(StInput, { props: { label: "x", modelValue: "" } })
    const el = await type(w, "a:b ")
    expect(el.value).toBe("a:b ")
    expect(last(w)).toBe("a:b ")
  })
})
