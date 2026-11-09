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
  it("is made only with the language of its voice-over, which goes to the core with it", async () => {
    const { w, commit } = opened({ AniStar: [] }, { AniStar: "rus" })
    await w.find("input.item-input").setValue("Оригинал")
    expect(createButton(w).attributes("disabled")).toBeDefined()
    await pickIn(w, ".group-language-new", t("voices.language.chi"))
    expect(createButton(w).attributes("disabled")).toBeUndefined()
    await createButton(w).trigger("click")
    await flushPromises()
    expect(commit).toHaveBeenCalledWith({ AniStar: [], Оригинал: [] }, { AniStar: "rus", Оригинал: "chi" })
  })

  it("made before languages were given is given one under its names, and a rename takes its language along", async () => {
    const { w, commit } = opened({ Old: [] }, {})
    await pickIn(w, ".item-select", "Old")
    expect(w.find(".group-language .selected-value").text()).toBe(t("voices.settings.group.languageNone"))
    await pickIn(w, ".group-language .item-select", t("voices.language.rus"))
    expect(commit).toHaveBeenLastCalledWith({ Old: [] }, { Old: "rus" })

    await w.setProps({ languages: { Old: "rus" } })
    await w.find(".btn-edit").trigger("click")
    await w.find("input.item-input").setValue("Новое")
    await w.find(".btn-confirm").trigger("click")
    await flushPromises()
    expect(commit).toHaveBeenLastCalledWith({ Новое: [] }, { Новое: "rus" })
  })
})
