// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { mount } from "@vue/test-utils"
import PrimeVue from "primevue/config"
import FoldButton from "./FoldButton.vue"

const mounted = (props: Record<string, unknown>) => mount(FoldButton, { props: { label: "Сбросить", icon: "pi pi-replay", ...props },
  global: { plugins: [PrimeVue] } })

describe("FoldButton", () => {
  it("is a small button with its word, its icon and the class of the tier it folds at (the middle one by default)", () => {
    const button = mounted({ severity: "secondary" })
    expect(button.classes()).toEqual(expect.arrayContaining(["fold-btn", "fold-md", "p-button"]))
    expect(button.find(".p-button-label").text()).toBe("Сбросить")
    expect(button.find(".pi-replay").exists()).toBe(true)
    expect(mounted({ fold: "lg" }).classes()).toContain("fold-lg")
    expect(mounted({ fold: "sm" }).classes()).toContain("fold-sm")
  })

  it("names itself by its hint, its title and its accessible name, and by its word when it has no hint", () => {
    const hinted = mounted({ hint: "Сбросить неудачные попытки серии" })
    expect(hinted.attributes("title")).toBe("Сбросить неудачные попытки серии")
    expect(hinted.attributes("aria-label")).toBe("Сбросить неудачные попытки серии")
    const plain = mounted({})
    expect(plain.attributes("title")).toBe("Сбросить")
    expect(plain.attributes("aria-label")).toBe("Сбросить")
  })

  it("passes a click and the disabled state to its button", async () => {
    let clicks = 0
    const button = mount(FoldButton, { props: { label: "Сбросить", icon: "pi pi-replay" }, attrs: { onClick: () => { clicks += 1 } },
      global: { plugins: [PrimeVue] } })
    await button.trigger("click")
    expect(clicks).toBe(1)
    const off = mount(FoldButton, { props: { label: "Сбросить", icon: "pi pi-replay" }, attrs: { disabled: true },
      global: { plugins: [PrimeVue] } })
    expect(off.attributes("disabled")).toBeDefined()
  })
})
