// Sample data of the forms page (/#gallery): what real data looks like, not texts of the window.
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
export const RICK_SLICED_TITLE = "Рик и Морти s07e03"
export const RICK_SLICED_FILE = "Рик и Морти s07e03.mkv"
export const MISSING_EPISODE = "s07e05"

export const SLICING_SAMPLE = {
  title: RICK_COMPILATION,
  active: ["00:00 (s07e01)", "22:14 (s07e02)", "44:30 (s07e03)"],
  garbage: ["00:00 (Интро)", "21:50 (Реклама блогера)"],
  status: "Готово к нарезке: 3 главы",
}

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

export const QUEUE_SLICING: { title: string; status: StateName; done: number; total: number }[] = [
  { title: SERIES_RICK, status: "slice", done: 2, total: 5 },
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
