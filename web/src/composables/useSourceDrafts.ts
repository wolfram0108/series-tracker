import { computed, ref, watch } from "vue"
import { t } from "../i18n"
import {
  draftBody, readEnded, viewOf,
  type LayoutRow, type PreviewReply, type SourceDraft, type SourceItem, type SourceSummary, type SourceType,
} from "../lib/sources"
import { useSourceTablesStore, type PageStatus } from "../stores/sourceTables"
import { previewAddress, readPageNow } from "./sourceCalls"
import { typeAddress } from "./sourceAddress"
import { useSSE } from "./useSSE"

interface Draft {
  key: string
  url: string
  sourceType: SourceType
  profileId: number | null
  vibixSeason: number | null
  playersOff: string[]
  voiceName: string
  /** The address the core names the page with in its events; known after the first preview. */
  canonical: string
  summary: SourceSummary | null
  /** The rows the rules of the profile made of the page, as the core's last complete answer gave them. */
  rows: LayoutRow[]
  asked: { state: PreviewReply["state"]; reason?: string | null } | null
  /** The news of the page that the last answer already knew; only news after it asks the preview again. */
  handled: PageStatus | null
}

export type DraftPatch = Partial<Pick<SourceItem, "url" | "profileId" | "vibixSeason" | "playersOff" | "voiceName">>
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
    vibixSeason: draft.vibixSeason,
    playersOff: draft.playersOff,
    voiceName: draft.voiceName,
    view: viewOf({
      summary: draft.summary, playersOff: draft.playersOff, rows: draft.rows, live: tables.forUrl(draft.canonical),
      asked: draft.asked,
    }),
  })))

  function find(key: string | number): Draft | undefined {
    return drafts.value.find((draft) => draft.key === key)
  }

  /** Ask the core about the draft; the answer sets what the window shows, a refusal is the words of the error. */
  async function ask(draft: Draft, refresh: boolean): Promise<void> {
    const before = tables.forUrl(draft.canonical)
    const answer = await previewAddress({
      url: draft.url, profile_id: draft.profileId, refresh,
      players_off: draft.playersOff, vibix_season: draft.vibixSeason, voice_name: draft.voiceName,
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
    // Rows come with a summary: a failed read that kept no complete table leaves the earlier rows in force.
    draft.rows = answer.data.summary ? (answer.data.rows ?? []) : draft.rows
    draft.handled = before
    await settle(draft)
  }

  /** A read that ended while the answer was on its way, or after it, asks the preview again once. */
  async function settle(draft: Draft): Promise<void> {
    if (readEnded(draft.asked, tables.forUrl(draft.canonical), draft.handled)) await ask(draft, false)
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
      const typed = await typeAddress(address)
      if (typed.kind !== "site") return typed
      const { sourceType, url } = typed
      if (drafts.value.some((draft) => draft.url === url)) {
        return { kind: "refused", text: t("sources.add.duplicate") }
      }
      const draft: Draft = {
        key: `draft-${++counter}`, url, sourceType, profileId: null, vibixSeason: null,
        playersOff: [], voiceName: "", canonical: "", summary: null, rows: [], asked: { state: "reading" }, handled: null,
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

  /** A choice of a draft. What changes the page asked for (the address) reads again; a profile or a choice of the source only recounts. */
  async function change(key: string | number, patch: DraftPatch): Promise<void> {
    const draft = find(key)
    if (!draft) return
    const rereads = patch.url !== undefined && patch.url !== draft.url
    // What a profile or a choice of the source changes is recounted from the table in hand, reading nothing.
    const recounts = (patch.profileId !== undefined && patch.profileId !== draft.profileId)
      || (patch.vibixSeason !== undefined && patch.vibixSeason !== draft.vibixSeason)
      || (patch.voiceName !== undefined && patch.voiceName !== draft.voiceName)
      || (patch.playersOff !== undefined && patch.playersOff.join("\u0000") !== draft.playersOff.join("\u0000"))
    Object.assign(draft, patch)
    if (rereads) {
      draft.summary = null
      draft.rows = []
      draft.asked = { state: "reading" }
      draft.canonical = ""
      await ask(draft, true)
    } else if (recounts) {
      await ask(draft, false)
    }
  }

  /** The page of a draft is read now, though its table is not old: the answer in hand is taken as the one to improve on. */
  async function reread(key: string | number): Promise<void> {
    const draft = find(key)
    if (!draft) return
    error.value = ""
    draft.asked = { state: "reading" }
    draft.handled = tables.forUrl(draft.canonical)
    const answer = await readPageNow(draft.url)
    if (!answer.ok) {
      error.value = answer.text
      draft.asked = { state: "failed", reason: null }
    }
  }

  function reset(): void {
    drafts.value = []
    error.value = ""
  }

  /** The sources as `POST /api/series` takes them. */
  const bodies = computed<SourceDraft[]>(() => items.value.map((item) => draftBody(item)))
  const failed = computed(() => items.value.some((item) => item.view.state === "failed"))

  return { items, bodies, failed, error, busy, add, remove, change, reread, reset }
}
