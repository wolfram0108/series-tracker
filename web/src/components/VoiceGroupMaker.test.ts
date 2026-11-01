// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

const client = vi.hoisted(() => ({ GET: vi.fn(), PUT: vi.fn() }))
vi.mock("../api/client", () => ({ api: client }))

import { t } from "../i18n"
import VoiceGroupMaker from "./VoiceGroupMaker.vue"

const view = { priority: ["cvh"], voice_groups: {}, default_priority: ["cvh"], rejected: [], player_groups: {} }

beforeEach(() => Object.values(client).forEach((fn) => fn.mockReset()))

describe("VoiceGroupMaker", () => {
  it("makes the group of the typed name from the names of the voice-over and empties the field", async () => {
    client.GET.mockResolvedValue({ data: view })
    client.PUT.mockResolvedValue({ data: { saved: true, problems: [], settings: view } })
    const w = mount(VoiceGroupMaker, { props: { names: ["Animy"] } })
    await w.find("input").setValue("Анми")
    await w.find("button").trigger("click")
    await flushPromises()
    expect(client.PUT).toHaveBeenCalledWith("/api/voices/settings", { body: { priority: ["cvh"], voice_groups: { Анми: ["Animy"] } } })
    expect((w.find("input").element as HTMLInputElement).value).toBe("")
  })

  it("keeps the typed name and shows the refusal when the group is not taken", async () => {
    client.GET.mockResolvedValue({ data: view })
    client.PUT.mockResolvedValue({ data: { saved: false, settings: view, problems: [{ field: "voice_groups", detail: { code: "trigger_is_group", params: {} } }] } })
    const w = mount(VoiceGroupMaker, { props: { names: ["Animy"] } })
    await w.find("input").setValue("Animy")
    await w.find("button").trigger("click")
    await flushPromises()
    expect((w.find("input").element as HTMLInputElement).value).toBe("Animy")
    expect(w.find(".group-maker-failure").exists()).toBe(true)
  })

  it("cannot make a group before a name is typed, and while it is disabled", async () => {
    const w = mount(VoiceGroupMaker, { props: { names: ["Animy"] } })
    expect(w.find("button").attributes("disabled")).toBeDefined()
    await w.find("input").setValue("A")
    expect(w.find("button").attributes("disabled")).toBeUndefined()
    await w.setProps({ disabled: true })
    expect(w.find("button").attributes("disabled")).toBeDefined()
    expect(w.find("input").attributes("disabled")).toBeDefined()
  })

  it("says to name the source when the voice-over has no name to put it into a group by", () => {
    const w = mount(VoiceGroupMaker, { props: { names: [] } })
    expect(w.find("input").exists()).toBe(false)
    expect(w.text()).toBe(t("voices.group.nameless"))
  })
})
