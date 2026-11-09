// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils"

vi.mock("../../composables/useConfirm", () => ({ useConfirm: () => ({ open: async () => ({ confirmed: true }) }) }))

import VoiceGroupsEditor from "./VoiceGroupsEditor.vue"
import { t } from "../../i18n"

let mounted: VueWrapper | null = null
afterEach(() => mounted?.unmount())

function opened(groups: Record<string, string[]>, languages: Record<string, string>) {
  const commit = vi.fn(async () => true)
  mounted = mount(VoiceGroupsEditor, { props: { groups, languages, busy: false, commit, problems: [], rejected: false } })
  return { w: mounted, commit }
}
const createButton = (w: VueWrapper) => w.find(".btn-add")
async function pickIn(w: VueWrapper, selector: string, label: string) {
  await w.find(`${selector}`).trigger("click")
  const option = [...document.body.querySelectorAll(".options-list .option")].find((each) => each.textContent?.trim() === label)
  ;(option as HTMLElement).click()
  await flushPromises()
}

describe("a group of voice-overs", () => {
  it("is made by its name alone; its language is chosen under it, marked red until then, and goes to the core", async () => {
    const { w, commit } = opened({ AniStar: [] }, { AniStar: "rus" })
    expect(w.findAll(".input-constructor-group").length).toBe(1) // the top row holds no choice of a language
    await w.find("input.item-input").setValue("Оригинал")
    await createButton(w).trigger("click")
    await flushPromises()
    expect(commit).toHaveBeenLastCalledWith({ AniStar: [], Оригинал: [] }, { AniStar: "rus" })

    await w.setProps({ groups: { AniStar: [], Оригинал: [] } })
    const language = () => w.find(".group-language")
    expect(language().classes()).toContain("is-invalid")
    expect(language().find(".selected-value").text()).toBe(t("voices.settings.group.languageNone"))
    await pickIn(w, ".group-language .item-select", t("voices.language.chi"))
    expect(commit).toHaveBeenLastCalledWith({ AniStar: [], Оригинал: [] }, { AniStar: "rus", Оригинал: "chi" })
    await w.setProps({ languages: { AniStar: "rus", Оригинал: "chi" } })
    expect(language().classes()).not.toContain("is-invalid")
  })

  it("keeps its language shown and taken along while it is renamed", async () => {
    const { w, commit } = opened({ Old: [] }, { Old: "rus" })
    await pickIn(w, ".item-select", "Old")
    await w.find(".btn-edit").trigger("click")
    expect(w.find(".group-language .selected-value").text()).toBe(t("voices.language.rus"))
    await w.find("input.item-input").setValue("Новое")
    await w.find(".btn-confirm").trigger("click")
    await flushPromises()
    expect(commit).toHaveBeenLastCalledWith({ Новое: [] }, { Новое: "rus" })
  })

  it("offers the languages of the core, without Ukrainian", async () => {
    const { w } = opened({ Old: [] }, {})
    await pickIn(w, ".item-select", "Old")
    await w.find(".group-language .item-select").trigger("click")
    const offered = [...document.body.querySelectorAll(".options-list .option")].map((each) => each.textContent?.trim())
    expect(offered).toEqual([t("voices.language.rus"), t("voices.language.eng"), t("voices.language.chi"),
                             t("voices.language.jpn"), t("voices.language.kor")])
  })
})
