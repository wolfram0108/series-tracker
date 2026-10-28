/** Russian catalog. Keys are semantic (`area.part.meaning`); screens ask for a key, never for text. */
export const ru = {
  "player.cvh": "CVH",
  "player.kodik": "Kodik",
  "add.source.label": "Плеер и озвучка",
  "add.source.player.cvh": "Плеер 1 (CVH)",
  "add.source.player.kodik": "Плеер 2 (Kodik)",
  "add.source.kind.voice": "озвучка",
  "add.source.kind.subtitles": "субтитры",
  "add.source.none": "нет серий",
  "add.source.coverage": "{ranges} ({count} сер.)",
  "add.source.option": "{player} · {studio}{kind} · {coverage}",
  "add.source.coverageHint": "Серии озвучки: {ranges} ({count} сер.)",
  "add.layout.title": "Озвучки",
  "add.layout.role.reference": "референс",
  "add.layout.role.dub": "дополнительная",
  "add.layout.row": "{role} · {player} · {coverage}",
  "add.layout.fallback": "запасной",
  "add.layout.fallbackRow": "{player} · {coverage}",
  "add.layout.skipped": "{studio} · {player} не качается: озвучка уже в референсе",
  "add.layout.hint": "Дополнительные озвучки скачиваются во временный каталог, выравниваются по референсу и кладутся рядом с ним.",
} as const

export type MessageKey = keyof typeof ru
