// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils"
import PrimeVue from "primevue/config"

// The tab «Торрент» (stage 15, step 9): the head tells the state of the release, the editors place what is picked with one
// request each, and what the core changes comes back as its events — never by asking again.
const calls = vi.hoisted(() => ({
  fetchPage: vi.fn(), fetchReleases: vi.fn(), fetchEditor: vi.fn(), downloadRelease: vi.fn(), watchRelease: vi.fn(),
  assign: vi.fn(), unassign: vi.fn(), setFolder: vi.fn(), deleteFolder: vi.fn(), autoLayout: vi.fn(), removeFromFolder: vi.fn(),
}))
vi.mock("../../../composables/torrentCalls", () => calls)
const sse = vi.hoisted(() => ({ handlers: new Map<string, (data: unknown) => void>() }))
vi.mock("../../../composables/useSSE", () => ({
  useSSE: () => ({ on: (event: string, handler: (data: unknown) => void) => { sse.handlers.set(event, handler); return () => sse.handlers.delete(event) } }),
}))
vi.mock("../../../composables/useWindowBusy", () => ({ useBusyBar: () => undefined }))

import { t, tn } from "../../../i18n"
import type { Editor, EditorView, ReleaseView } from "../../../lib/torrent"
import type { SeriesSource } from "../../../lib/sources"
import TorrentTab from "./TorrentTab.vue"
import TorrentFiles from "./TorrentFiles.vue"
import TorrentTracks from "./TorrentTracks.vue"
import TorrentStreams from "./TorrentStreams.vue"

const URL = "https://kinozal.tv/details.php?id=7"
const ok = <T>(data: T) => ({ ok: true as const, data })
const source = { id: 3, series_id: 1, position: 2, url: URL, source_type: "torrent", color: 2 } as unknown as SeriesSource
const kept = (state: ReleaseView["state"], progress = 1): ReleaseView => ({ url: URL, release: "page", title: "Сериал / WEB-DL", state,
  watched: true, pending: false, files: [{ path: "S/E01.mkv", size: 1, progress, priority: 1 }, { path: "S/E02.mkv", size: 1, progress: 1, priority: 1 }] })
// The line of a file as the core makes it (core/contracts/torrent.py files_line).
const fileLine = (path: string, kind: string) => {
  const at = path.lastIndexOf("/")
  return `D:${path.slice(0, at)} | ${path.slice(at + 1)} | K:${{ video: "видео", audio: "звук", subtitles: "субтитры", font: "шрифт" }[kind]} | X:${path.split(".").pop()}`
}
const file = (path: string, kind: "video" | "audio" | "subtitles" | "font", place: [number, number] | null, by: string | null = null) => ({
  item: `page/${path}`, path, line: fileLine(path, kind), kind, size: 1, priority: 1,
  place: place && { season: place[0], episode: place[1], last: null }, by })
const view = (overrides: Partial<EditorView> = {}): EditorView => ({
  releases: [{ release: "page", title: "", state: "ready", pending: false, reading: null }],
  tabs: {
    files: [file("S/E01.mkv", "video", [1, 1], "Серия · 1"), file("Bonus/OVA 2.mkv", "video", null), file("Bonus/OVA 1.mkv", "video", null),
      file("S/fonts/a.ttf", "font", null)],
    voices: [{ item: "page/S/JAM/E01.mka", line: "D:S/JAM | E01.mka | X:mka | S:S01E01", kind: "audio", folder: "JAM", by: "Озвучка · 1", waiting: false, language: "rus",
      subtitle_type: "", path: "S/JAM/E01.mka", place: { season: 1, episode: 1, last: null }, stream: null },
    { item: "page/S/Mil/E01.mka", line: "D:S/Mil | E01.mka | X:mka | S:S01E01", kind: "audio", folder: null, by: null, waiting: true, language: "rus", subtitle_type: "",
      path: "S/Mil/E01.mka", place: { season: 1, episode: 1, last: null }, stream: null }],
    subtitles: [], voice_streams: [], subtitle_streams: [],
  },
  folders: [{ kind: "voice", name: "JAM", items: 1, manual: false, language: "rus", subtitle_type: "", fonts: [] },
    { kind: "voice", name: "Милирина", items: 0, manual: true, language: "rus", subtitle_type: "", fonts: [] }],
  unplaced: { files: 2, voices: 1, subtitles: 0, voice_streams: 0, subtitle_streams: 0 },
  containers: [],
  hidden: [],
  removed: [],
  ...overrides,
})
const editor = (overrides: Partial<EditorView> = {}): Editor => ({ url: URL, profiles: { files: 11, voices: 12, subtitles: 13, voice_streams: 14, subtitle_streams: 15 },
  view: view(overrides) })

let mounted: VueWrapper | null = null
const mountWith = (component: unknown, props: Record<string, unknown>) => {
  mounted = mount(component as never, { props: props as never, global: { plugins: [PrimeVue], stubs: { RulesEditor: true } } })
  return mounted
}
// jsdom has no matchMedia; the list of PrimeVue (the language of a folder) asks it of the orientation of the screen.
window.matchMedia ??= ((query: string) => ({ matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {} })) as never
beforeEach(() => {
  sse.handlers.clear()
  for (const each of Object.values(calls)) each.mockReset().mockResolvedValue(ok({}))
})
afterEach(() => mounted?.unmount())

describe("the head of the tab", () => {
  it("offers the release of the page to download while none is, with no editor to switch to", async () => {
    calls.fetchReleases.mockResolvedValue(ok({ releases: [] }))
    calls.fetchEditor.mockResolvedValue(ok(editor()))
    calls.fetchPage.mockResolvedValue(ok({ url: URL, site: "kinozal.tv", title: "Сериал", releases: [{ release: "page", title: "Сериал / WEB-DL" }] }))
    const w = mountWith(TorrentTab, { sources: [source] })
    await flushPromises()
    expect(w.find(".torrent-state").text()).toBe("Не загружено")
    expect(w.find(".torrent-switch").exists()).toBe(false)
    await w.find(".do-download").trigger("click")
    expect(calls.downloadRelease).toHaveBeenCalledWith(URL, "page")
  })

  it("follows the release the core tells of: its download, then its read, then what waits for the operator", async () => {
    calls.fetchReleases.mockResolvedValue(ok({ releases: [kept("downloading", 0.5)] }))
    calls.fetchEditor.mockResolvedValue(ok(editor()))
    const w = mountWith(TorrentTab, { sources: [source] })
    await flushPromises()
    expect(w.find(".torrent-state").text()).toBe("Загрузка")
    expect(w.find(".torrent-progress").text()).toBe(t("torrent.progress.files", { done: 1, total: 2, share: 75 }))
    expect(calls.fetchPage).not.toHaveBeenCalled()

    sse.handlers.get("torrent_release_update")!(kept("ready"))
    sse.handlers.get("torrent_read_update")!({ url: URL, release: "page", done: 1, total: 2 })
    await flushPromises()
    expect(w.find(".torrent-state").text()).toBe("Разбор раздачи")
    sse.handlers.get("torrent_read_update")!({ url: URL, release: "page", done: 2, total: 2 })
    await flushPromises()
    expect(w.find(".torrent-state").text()).toBe("Готово")
    expect(w.find(".torrent-waiting").text()).toBe(t("torrent.progress.waiting", { n: 3 }))
    expect(w.findAll(".torrent-switch .st-tab").map((tab) => tab.text())).toEqual(["Файлы2", "Озвучки1", "Субтитры", "Озв. конт.", "Суб. конт."])
  })

  it("reads the editor again when the core says it changed, and the automatic update is one request", async () => {
    calls.fetchReleases.mockResolvedValue(ok({ releases: [kept("ready")] }))
    calls.fetchEditor.mockResolvedValue(ok(editor()))
    const w = mountWith(TorrentTab, { sources: [source] })
    await flushPromises()
    expect(calls.fetchEditor).toHaveBeenCalledTimes(1)
    sse.handlers.get("torrent_editor_update")!({ url: "https://other" })
    sse.handlers.get("torrent_editor_update")!({ url: URL })
    await flushPromises()
    expect(calls.fetchEditor).toHaveBeenCalledTimes(2)
    await w.find(".torrent-watch input").setValue(false)
    expect(calls.watchRelease).toHaveBeenCalledWith(URL, "page", false)
  })
})

describe("the editor «Файлы»", () => {
  it("lays the files picked on a season in the episodes after its last one, in the order of their names", async () => {
    const w = mountWith(TorrentFiles, { editor: editor() })
    const bonus = w.findAll(".torrent-row.is-group").find((row) => row.text().startsWith("D:Bonus"))!
    await bonus.find("input[type=checkbox]").setValue(true)
    expect(w.find(".torrent-pick").text()).toContain(t("torrent.select.count", { n: 2 }))
    const season = w.find(".drop-season")
    expect(season.text()).toBe(t("torrent.draft.dropSeason", { season: 1, files: tn("torrent.files", 2), from: 2 }))
    await season.trigger("click")
    expect(calls.assign).toHaveBeenCalledWith({ url: URL, tab: "files", items: ["page/Bonus/OVA 1.mkv", "page/Bonus/OVA 2.mkv"],
      season: 1, episode: 2, in_order: true })
  })

  it("paints a row by its state, leaves nothing out and tells the files hidden by the filter", async () => {
    const w = mountWith(TorrentFiles, { editor: editor() })
    expect(w.text()).toContain(t("torrent.files.hidden", { filter: t("torrent.filter.unplaced"), list: "S (1), S/fonts (1)" }))
    // No «Не брать» anywhere: a file the rules and the hand gave no episode is red and goes no further.
    expect(w.find(".drop-bin").exists()).toBe(false)
    expect(w.findAll(".file-row").map((row) => row.classes().filter((each) => ["is-placed", "is-unplaced", "is-notInvolved"].includes(each)))).toEqual([["is-unplaced"], ["is-unplaced"]])
    expect(w.find(".torrent-dot").exists()).toBe(false)
  })
})

describe("the editors of the tracks", () => {
  it("place the tracks picked in a folder, a folder made by hand empty until then", async () => {
    const w = mountWith(TorrentTracks, { editor: editor(), tab: "voices" })
    const folders = w.findAll(".torrent-folder")
    expect(folders.map((each) => each.find(".torrent-folder-name").text())).toEqual(["JAM", "Милирина"])
    expect(folders[0].find(".torrent-rule").text()).toBe(t("torrent.by.row", { rule: "Озвучка", row: 1 }))
    expect(folders[1].find(".torrent-hand").text()).toBe(t("torrent.by.manual"))
    // Every folder may be deleted, that of a rule too: its tracks are then in no folder until «Авто».
    expect(folders.map((each) => each.find(".do-delete-folder").exists())).toEqual([true, true])
    await folders[0].find(".do-delete-folder").trigger("click")
    await flushPromises()
    expect(calls.deleteFolder).toHaveBeenCalledWith(URL, "voice", "JAM", {}, true)
    expect(calls.assign).not.toHaveBeenCalled()
    const unplaced = w.findAll(".track-row").find((row) => row.text().includes("D:S/Mil"))!
    await unplaced.find("input").setValue(true)
    await w.findAll(".drop-folder").at(-1)!.trigger("click")
    expect(calls.assign).toHaveBeenCalledWith({ url: URL, tab: "voices", items: ["page/S/Mil/E01.mka"], folder: "Милирина" })
  })

  it("show a folder as a row: where it comes from, its episodes and tracks; unfolded, the tracks; the bin takes back a hand's", async () => {
    const inner = { item: "page/S/E01.mkv#1", line: "T:звук | A:1 | N:Original | C:aac", kind: "audio" as const, folder: "Original", by: "meta",
      waiting: true, language: "und", subtitle_type: "", path: "S/E01.mkv", place: { season: 1, episode: 1, last: null },
      stream: { index: 1, kind: "audio", codec: "aac", title: "Original" } }
    // A track of another episode whose file names it otherwise, put in the folder by hand.
    const handed = { ...inner, item: "page/S/E02.mkv#2", line: "T:звук | A:2 | L:rus | N:Studio | C:aac", by: "manual", waiting: false,
      language: "rus", path: "S/E02.mkv", place: { season: 1, episode: 2, last: null } }
    const w = mountWith(TorrentTracks, { editor: editor({ tabs: { ...view().tabs, voice_streams: [inner, handed] },
      folders: [...view().folders, { kind: "voice", name: "Original", items: 2, manual: false, language: "", subtitle_type: "", fonts: [] }],
      containers: [{ item: "page/S/E01.mkv", path: "S/E01.mkv", place: { season: 1, episode: 1, last: null },
        streams: [{ index: 0, kind: "video" }, { index: 1, kind: "audio", codec: "aac" }] }] }), tab: "voices" })
    const original = w.find(".torrent-folder[data-folder='Original']")
    // The folder says where it comes from — the titles in the files — and each track, on its row, where it got its folder.
    expect(original.findAll(".torrent-folders-sources > span").map((each) => each.text())).toEqual([t("torrent.folder.fromFile")])
    expect(original.findAll(".torrent-folders-num").map((cell) => cell.text())).toEqual(["1–2", "2"])
    // The language of the folder is set here.
    const language = original.find("select.torrent-language")
    await language.setValue("jpn")
    expect(calls.setFolder).toHaveBeenCalledWith({ url: URL, kind: "voice", name: "Original", language: "jpn", subtitle_type: "" })
    await original.find(".torrent-folders-chev").trigger("click")
    // Unfolded, a track is its episode, its stream as the left pane names it, and «вручную» or «авто»; every track has its
    // «−», which takes it out of the folder — red — whoever put it there.
    const tracks = w.findAll(".torrent-folder-track")
    expect(tracks.map((each) => [each.find(".torrent-folders-place").text(), each.find(".torrent-folders-id").text(),
      each.find(".torrent-hand, .torrent-auto").text()])).toEqual([["s01e01", "A:1", t("torrent.by.auto")], ["s01e02", "A:2", t("torrent.by.manual")]])
    expect(tracks.map((each) => each.find(".torrent-folders-file").exists())).toEqual([false, false])
    // A track a rule placed says the rule, not «авто».
    await w.find(".torrent-folder[data-folder='JAM'] .torrent-folders-chev").trigger("click")
    const ruled = w.findAll(".torrent-folder-track").find((each) => each.find(".torrent-folders-place").text() === "s01e01"
      && each.find(".torrent-rule").exists())!
    expect(ruled.find(".torrent-rule").text()).toBe(t("torrent.by.row", { rule: "Озвучка", row: 1 }))
    expect(tracks.map((each) => each.find(".do-drop-track").exists())).toEqual([true, true])
    for (const [index, item] of [[0, "page/S/E01.mkv#1"], [1, "page/S/E02.mkv#2"]] as const) {
      await tracks[index].find(".do-drop-track").trigger("click")
      await flushPromises()
      expect(calls.removeFromFolder).toHaveBeenCalledWith(URL, "voice_streams", [item])
    }
    expect(calls.unassign).not.toHaveBeenCalled()
    // The folder deleted takes the hand's placement in it; the automatic layout does not make it again until «Авто».
    await original.find(".do-delete-folder").trigger("click")
    await flushPromises()
    expect(calls.deleteFolder).toHaveBeenCalledWith(URL, "voice", "Original", { voice_streams: ["page/S/E02.mkv#2"] }, true)
    expect(calls.assign).not.toHaveBeenCalled()
  })

  it("paint a track by its state: red in no folder (it goes no further), green placed, the video plain; no dots", async () => {
    const streams = [{ index: 0, kind: "video", line: "T:видео | A:0 | C:h264" }, { index: 3, kind: "subtitle", line: "T:субтитры | A:3 | L:rus | N:Надписи | C:ass" },
      { index: 4, kind: "subtitle", line: "T:субтитры | A:4 | N:Субтитры | C:ass" }]
    const entry = (index: number, folder: string | null) => ({ item: `page/S/E04.mkv#${index}`, line: streams.find((each) => each.index === index)!.line,
      kind: "subtitles" as const, folder, by: folder ? "meta" : null, waiting: !folder, language: folder ? "rus" : "", subtitle_type: "",
      path: "S/E04.mkv", place: { season: 1, episode: 4, last: null }, stream: streams.find((each) => each.index === index)! })
    const w = mountWith(TorrentStreams, { editor: editor({ tabs: { ...view().tabs, subtitle_streams: [entry(3, "Надписи"), entry(4, null)] },
      containers: [{ item: "page/S/E04.mkv", path: "S/E04.mkv", place: { season: 1, episode: 4, last: null }, streams }] }), tab: "subtitle_streams" })
    expect(w.findAll(".stream-row").map((row) => row.classes().filter((each) => ["is-placed", "is-unplaced", "is-notInvolved"].includes(each))))
      .toEqual([["is-notInvolved"], ["is-placed"], ["is-unplaced"]])
    expect(w.find(".torrent-dot").exists()).toBe(false)
    expect(w.findAll(".torrent-filter").map((each) => each.text())).toEqual([`${t("torrent.filter.allTracks")} 3`,
      `${t("torrent.filter.noFolder")} 1`, `${t("torrent.filter.inFolder")} 1`, `${t("torrent.filter.video")} 1`])
  })

  it("«Авто» makes again the folders of the automatic layout the user deleted, and is idle when none is", async () => {
    const idle = mountWith(TorrentTracks, { editor: editor(), tab: "voices" })
    expect(idle.find(".do-auto").attributes("disabled")).toBeDefined()
    const w = mountWith(TorrentTracks, { editor: editor({ hidden: [{ kind: "voice", name: "Original" }] }), tab: "voices" })
    await w.find(".do-auto").trigger("click")
    await flushPromises()
    expect(calls.autoLayout).toHaveBeenCalledWith(URL, ["voice"])
    expect(calls.unassign).not.toHaveBeenCalled()
  })

  it("show the containers of one composition as one, a track of it standing for that track of every file", async () => {
    const streams = () => [{ index: 0, kind: "video", codec: "h264", height: 1080, line: "T:видео | A:0 | C:h264" },
      { index: 1, kind: "audio", codec: "ac3", channels: 2, language: "rus", title: "", line: "T:звук | A:1 | L:rus | C:ac3" }]
    const track = (n: number) => ({ item: `page/S/E0${n}.mkv#1`, line: "", kind: "audio" as const, folder: null, by: null, waiting: true,
      language: "rus", subtitle_type: "", path: `S/E0${n}.mkv`, place: { season: 0, episode: n, last: null }, stream: streams()[1] })
    const w = mountWith(TorrentStreams, { editor: editor({
      tabs: { ...view().tabs, voice_streams: [track(1), track(2)] },
      containers: [1, 2].map((n) => ({ item: `page/S/E0${n}.mkv`, path: `S/E0${n}.mkv`, place: { season: 0, episode: n, last: null }, streams: streams() })),
    }), tab: "voice_streams" })
    expect(w.find(".torrent-row.is-group").text()).toContain(t("torrent.streams.group", { season: 0, episodes: "1–2" }))
    const rows = w.findAll(".stream-row")
    // Each track shows its raw line, as the rules of the tab read it, and nothing of where it went.
    expect(rows.map((row) => row.find(".torrent-line").text())).toEqual(["T:видео | A:0 | C:h264", "T:звук | A:1 | L:rus | C:ac3"])
    await rows[1].find("input").setValue(true)
    expect(w.find(".torrent-pick").text()).toContain(t("torrent.select.count", { n: 2 }))
    await w.findAll(".drop-folder").at(-1)!.trigger("click")
    expect(calls.assign).toHaveBeenCalledWith({ url: URL, tab: "voice_streams", items: ["page/S/E01.mkv#1", "page/S/E02.mkv#1"], folder: "Милирина" })
  })
})
