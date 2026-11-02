import { beforeEach, describe, expect, it, vi } from "vitest"

const client = vi.hoisted(() => ({ GET: vi.fn(), PUT: vi.fn() }))
vi.mock("../api/client", () => ({ api: client }))

import { t } from "../i18n"
import { useVoiceGroups } from "./useVoiceGroups"

const view = (groups: Record<string, string[]>) => ({
  priority: ["cvh", "kodik"], voice_groups: groups, default_priority: ["cvh", "kodik"], rejected: [], player_groups: {},
})

beforeEach(() => Object.values(client).forEach((fn) => fn.mockReset()))

describe("useVoiceGroups", () => {
  it("saves the group with the names it is made from as its triggers, next to the groups there are", async () => {
    client.GET.mockResolvedValueOnce({ data: view({ AniStar: ["anistar"] }) })
    client.PUT.mockResolvedValueOnce({ data: { saved: true, problems: [], settings: view({}) } })
    const groups = useVoiceGroups()
    expect(await groups.makeGroup("  Студия  ", ["Студия Исаева"])).toBeNull()
    expect(client.PUT).toHaveBeenCalledWith("/api/voices/settings", {
      body: { priority: ["cvh", "kodik"], voice_groups: { AniStar: ["anistar"], Студия: ["Студия Исаева"] } },
    })
    expect(groups.busy.value).toBe(false)
  })

  it("adds the names to a group of that name and keeps the triggers it has", async () => {
    client.GET.mockResolvedValueOnce({ data: view({ AniStar: ["anistar"] }) })
    client.PUT.mockResolvedValueOnce({ data: { saved: true, problems: [], settings: view({}) } })
    await useVoiceGroups().makeGroup("AniStar", ["Anistar 2", "anistar"])
    expect(client.PUT.mock.calls[0][1].body.voice_groups).toEqual({ AniStar: ["anistar", "Anistar 2"] })
  })

  it("leaves out of the triggers the names the name of the group covers by itself", async () => {
    client.GET.mockResolvedValueOnce({ data: view({}) })
    client.PUT.mockResolvedValueOnce({ data: { saved: true, problems: [], settings: view({}) } })
    await useVoiceGroups().makeGroup("AniStar", ["AniStar", "anistar ", "Ani-Star", "AniStar Многоголосый"])
    expect(client.PUT.mock.calls[0][1].body.voice_groups).toEqual({ AniStar: ["AniStar Многоголосый"] })
  })

  it("says in words why the core did not take the group", async () => {
    client.GET.mockResolvedValueOnce({ data: view({}) })
    client.PUT.mockResolvedValueOnce({ data: {
      saved: false, settings: view({}),
      problems: [{ field: "voice_groups", detail: { code: "trigger_is_group", params: { trigger: "AniStar", group: "AniStar" } } }],
    } })
    const refused = await useVoiceGroups().makeGroup("AniStar", ["AniStar"])
    expect(refused).toBeTruthy()
    expect(refused).not.toBe(t("voices.settings.saveFailed"))
  })

  it("asks nothing for an empty name, and says so when the settings cannot be read or saved", async () => {
    const groups = useVoiceGroups()
    expect(await groups.makeGroup("   ", ["x"])).toBe(t("voices.group.nameEmpty"))
    expect(client.GET).not.toHaveBeenCalled()
    client.GET.mockResolvedValueOnce({ data: undefined })
    expect(await groups.makeGroup("A", ["x"])).toBe(t("voices.settings.loadFailed"))
    client.GET.mockResolvedValueOnce({ data: view({}) })
    client.PUT.mockResolvedValueOnce({ data: undefined })
    expect(await groups.makeGroup("A", ["x"])).toBe(t("voices.settings.saveFailed"))
    client.GET.mockRejectedValueOnce(new Error("offline"))
    expect(await groups.makeGroup("A", ["x"])).toBe(t("voices.settings.saveFailed"))
    expect(groups.busy.value).toBe(false)
  })
})
