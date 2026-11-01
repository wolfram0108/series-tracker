import { computed, ref, watch } from "vue"
import { t } from "../i18n"
import {
  draftBody, joinVkAddress, readOptions, sourceTypeOf, splitVkAddress, viewOf, vkModeOf,
  type PreviewReply, type SourceDraft, type SourceItem, type SourceSummary, type SourceType,
} from "../lib/sources"
import { useSourceTablesStore, type PageStatus } from "../stores/sourceTables"
import { previewAddress, resolveAddress } from "./sourceCalls"
import { useSSE } from "./useSSE"

interface Draft {
  key: string
  url: string
  sourceType: SourceType
  profileId: number | null
  vkSearchMode: string
  vibixSeason: number | null
  playersOff: string[]
  /** The address the core names the page with in its events; known after the first preview. */
  canonical: string
  summary: SourceSummary | null
  asked: { state: PreviewReply["state"]; reason?: string | null } | null
  /** The news of the page that the last answer already knew; only news after it asks the preview again. */
  handled: PageStatus | null
}

export type DraftPatch = Partial<Pick<SourceItem, "url" | "profileId" | "vkSearchMode" | "vibixSeason" | "playersOff">>
/** `kind`: the address is no site's, so it is not a video source (the window goes on as with a torrent). */
export type AddOutcome = { kind: "added" } | { kind: "other" } | { kind: "refused"; text: string }

let counter = 0

/**
 * The sources of a series that does not exist yet: they pile up until the series is added. Each draft asks the core
 * what its page is (`/api/sources/preview`); the reading goes on in the core and its end comes as an event, which
 * is the only thing that asks the preview again.
 */
export function useSourceDrafts() {
  const tables = useSourceTablesStore()
  const drafts = ref<Draft[]>([])
  const error = ref("")
  const busy = ref(false)

  const items = computed<SourceItem[]>(() => drafts.value.map((draft) => ({
    id: draft.key,
    url: draft.url,
    sourceType: draft.sourceType,
    profileId: draft.profileId,
    vkSearchMode: draft.vkSearchMode,
    vibixSeason: draft.vibixSeason,
    playersOff: draft.playersOff,
    view: viewOf({
      summary: draft.summary, playersOff: draft.playersOff, live: tables.forUrl(draft.canonical), asked: draft.asked,
    }),
  })))

  function find(key: string | number): Draft | undefined {
    return drafts.value.find((draft) => draft.key === key)
  }

  /** Ask the core about the draft; the answer sets what the window shows, a refusal is the words of the error. */
  async function ask(draft: Draft, refresh: boolean): Promise<void> {
    const before = tables.forUrl(draft.canonical)
    const answer = await previewAddress({
      url: draft.url, options: readOptions(draft), profile_id: draft.profileId, refresh,
    })
    if (!drafts.value.includes(draft)) return
    if (!answer.ok) {
      error.value = answer.text
      draft.asked = { state: "failed", reason: null }
      return
    }
    draft.canonical = answer.data.url
    draft.asked = { state: answer.data.state, reason: answer.data.reason }
    draft.summary = answer.data.summary ?? draft.summary
    draft.handled = before
    await settle(draft)
  }

  /** A read that ended while the answer was on its way, or after it, asks the preview again once. */
  async function settle(draft: Draft): Promise<void> {
    const news = tables.forUrl(draft.canonical)
    if (draft.asked?.state !== "reading" || !news || news === draft.handled || news.state === "reading") return
    await ask(draft, false)
  }

  watch(() => tables.pages, () => {
    for (const draft of drafts.value) void settle(draft)
  })

  // A connection that came back may have missed the end of a read: a draft still reading asks again, reading nothing.
  watch(useSSE().connected, (up) => {
    if (!up) return
    for (const draft of drafts.value) if (draft.asked?.state === "reading") void ask(draft, false)
  })

  /** Make a draft of a typed address; the outcome says whether it is one (`other`: no site reads it). */
  async function add(raw: string): Promise<AddOutcome> {
    error.value = ""
    const address = raw.trim()
    if (!address) return { kind: "refused", text: "" }
    busy.value = true
    try {
      const answer = await resolveAddress(address)
      if (!answer.ok) return { kind: "refused", text: answer.text }
      const sourceType = sourceTypeOf(answer.data)
      if (sourceType === null) return { kind: "other" }
      const vk = sourceType === "vk_video" ? splitVkAddress(address) : null
      const url = vk ? joinVkAddress(vk.channel, vk.query) : (answer.data.canonical_url ?? address)
      if (drafts.value.some((draft) => draft.url === url)) {
        return { kind: "refused", text: t("sources.add.duplicate") }
      }
      const draft: Draft = {
        key: `draft-${++counter}`, url, sourceType, profileId: null, vkSearchMode: vkModeOf(null), vibixSeason: null,
        playersOff: [], canonical: "", summary: null, asked: { state: "reading" }, handled: null,
      }
      drafts.value.push(draft)
      await ask(drafts.value[drafts.value.length - 1], true)
      return { kind: "added" }
    } finally {
      busy.value = false
    }
  }

  function remove(key: string | number): void {
    drafts.value = drafts.value.filter((draft) => draft.key !== key)
  }

  /** A choice of a draft. What changes the page asked for (address, mode) reads again; a profile only recounts. */
  async function change(key: string | number, patch: DraftPatch): Promise<void> {
    const draft = find(key)
    if (!draft) return
    const rereads = (patch.url !== undefined && patch.url !== draft.url)
      || (patch.vkSearchMode !== undefined && patch.vkSearchMode !== draft.vkSearchMode)
    const reprofiles = patch.profileId !== undefined && patch.profileId !== draft.profileId
    Object.assign(draft, patch)
    if (rereads) {
      draft.summary = null
      draft.asked = { state: "reading" }
      draft.canonical = ""
      await ask(draft, true)
    } else if (reprofiles) {
      await ask(draft, false)
    }
  }

  function reset(): void {
    drafts.value = []
    error.value = ""
  }

  /** The sources as `POST /api/series` takes them. */
  const bodies = computed<SourceDraft[]>(() => items.value.map((item) => draftBody(item)))
  const failed = computed(() => items.value.some((item) => item.view.state === "failed"))

  return { items, bodies, failed, error, busy, add, remove, change, reset }
}
