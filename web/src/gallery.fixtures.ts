// Sample data of the forms page (/#gallery): what real data looks like, not texts of the window.
import type { ReadRow } from "./components/status/output/ReadProgress.vue"
import type { Held } from "./lib/plan"
import type { components } from "./api/schema"
import type { QueueRow, SourceMark } from "./lib/output"
import type { StateName } from "./lib/states"

export const SERIES_FRONT = "Фронт кровавой блокады"
export const SERIES_STRANGE = "Очень странные дела"
export const SERIES_FROM = "Извне"
export const SERIES_RICK = "Рик и Морти"

export const FRONT_EPISODE_TITLE = "Фронт кровавой блокады s01e01"
export const RICK_EPISODE_TITLE = "Рик и Морти s07e01"
export const RICK_RECEIVED_FILE = "Рик и Морти 7 сезон 1 серия [AniLibria].mp4"
export const RICK_EPISODE_FILE = "Рик и Морти s07e01.mkv"
export const RICK_TEST_APPLIED = "Рик и Морти 7 сезон 1 серия [AniLibria] 1080p"
export const RICK_TEST_EXCLUDED = "Рик и Морти 7 сезон [трейлер]"
export const EXCLUDED_BY_RULE = "правило «трейлер»"
export const RICK_COMPILATION = "Рик и Морти 7 сезон [компиляция].mkv"
export const MISSING_EPISODE = "s07e05"

export const ROWS: { name: string; status: StateName; progress: number }[] = [
  { name: SERIES_FRONT, status: "download", progress: 45 },
  { name: SERIES_FROM, status: "done", progress: 100 },
  { name: SERIES_RICK, status: "queued", progress: 0 },
]

export const LOGS = [
  { time: "18.06.2026, 00:34:41", group: "gateway", level: "INFO", msg: "SSE клиент подключился (count=1)" },
  { time: "18.06.2026, 00:33:01", group: "https", level: "INFO", msg: "HTTP Request: GET /api/scanner/status \"HTTP/1.1 200 OK\"" },
  { time: "18.06.2026, 00:32:39", group: "torrents", level: "WARNING", msg: "серия 1: торрент отсутствует в qBit — запись сброшена" },
  { time: "18.06.2026, 00:31:10", group: "scan", level: "INFO", msg: "запущено модулей: 13" },
]

export const QUEUE_PROCESSING: { title: string; torrent: number; hash: string; stage: StateName }[] = [
  { title: SERIES_FRONT, torrent: 12, hash: "974f83ee", stage: "renaming" },
]

export const QUEUE_DOWNLOAD: {
  file: string; status: StateName; cls: string; progress: number; speed: string; eta: string; remux: boolean
}[] = [
  { file: "Рик и Морти s07e01.mp4", status: "download", cls: "pill-primary", progress: 62, speed: "2.1 MB/s", eta: "1:20", remux: false },
  { file: "Извне s01e04.mp4", status: "remux", cls: "pill-info", progress: 90, speed: "", eta: "0:10", remux: true },
]

export const QUEUE_MONITOR: {
  title: string; hash: string; status: StateName; progress: number; speed: string; eta: string
}[] = [
  { title: SERIES_FROM, hash: "a1b2c3d4", status: "download", progress: 48, speed: "2.1 MB/s", eta: "1:20" },
]

export const ROOT_PATH = "/nas/media/Сериалы"
export const PASSWORD_SAMPLE = "секрет123"
export const DISABLED_SAMPLE = "нельзя"
export const PROFILE_DEFAULT = "Профиль по умолчанию"
export const PROFILE_ANIME = "Аниме"
export const PROFILE_MOVIES = "Кино"

// The file a command «Удалить» of the plan names, and the reads of the pages of the sources under way (the panel of a series).
export const CHIP_FILE = "s01e12 [Китайский] 1080p.mkv"
export const READS: ReadRow[] = [
  { key: 1, mark: { n: 2, color: 1, place: "old.yummyani.me" }, place: "old.yummyani.me", phase: "Опрос плееров", current: 150, total: 200, rate: 3.5 },
  { key: 2, mark: { n: 4, color: 2, place: "old.yummyani.me" }, place: "old.yummyani.me", phase: "Опрос плееров", current: 208, total: 1036, rate: 6.9 },
  { key: 3, mark: { n: 5, color: 3, place: "1.ruchime.org" }, place: "1.ruchime.org", phase: "Страница релиза", current: 0, total: 0, rate: null },
]

type QueueView = components["schemas"]["QueueView"]

// The queue of the reference of an episode with variants declared equal (Р-99): the file holds the video of a source the last read no
// longer gives (stale), the plan takes it, and nine variants of one picture stand in one group. The second group is made up.
const sameRow = (source_id: number, player: string, height: number, over: Partial<QueueRow> = {}): QueueRow => ({
  group: "Китайский", source_id, player: `line:${player}`, player_label: player, height, episodes: "481", stale: "", allowed: true,
  allowed_level: "default", language: "", subtitle_type: "", default: false, forced: false, same: null, ...over,
})
export const SAME_QUEUE: QueueView = {
  order_level: "season", same_level: "series",
  rows: [
    sameRow(108, "duanju", 2160, { stale: "481", same: 1 }), sameRow(106, "JD4K", 2160, { same: 1 }), sameRow(106, "zijianm3u8", 2160, { same: 1 }),
    sameRow(107, "JD4K", 2160, { same: 1 }), sameRow(109, "JD4K", 2160, { same: 1 }), sameRow(110, "JD4K", 2160),
    sameRow(106, "bfzym3u8", 1080), sameRow(107, "lzm3u8", 1080, { same: 2 }), sameRow(107, "bfzym3u8", 1080, { same: 2 }),
  ],
}
export const SAME_MARKS: Record<number, SourceMark> = {
  106: { n: 5, color: 4, place: "app://mtq/313" }, 107: { n: 6, color: 5, place: "app://gulu/1291" },
  108: { n: 7, color: 6, place: "app://appmn/1522016" }, 109: { n: 8, color: 7, place: "app://juquan/191" },
  110: { n: 9, color: 8, place: "app://juzi/164312" },
}
export const SAME_HELD: Held = { source_id: 108, player: "line:duanju" }
