import { describe, expect, it } from "vitest"
import { actionOptions } from "./ruleVocabulary"

const offered = (place?: Parameters<typeof actionOptions>[0]) => actionOptions(place).map((each) => each.value)

describe("the actions «ТО» offers where the constructor stands", () => {
  it("the section «Правила» of the series offers the actions of a series, none of the torrent editor", () => {
    expect(offered()).toEqual(["exclude", "extract_single", "extract_range", "extract_season", "assign_voiceover",
      "assign_episode", "assign_season", "assign_part", "assign_quality", "assign_resolution", "assign_range"])
  })

  it("«Файлы» of the torrent offers only the season, the episode and the tag of the version of a file", () => {
    expect(offered("files")).toEqual(["extract_single", "extract_range", "extract_season", "assign_episode", "assign_season",
      "assign_video_tag"])
  })

  it("the sound offers its folder and language, the subtitles their folder, language and type", () => {
    expect(offered("voices")).toEqual(["assign_voice_folder", "assign_language"])
    expect(offered("subtitles")).toEqual(["assign_subtitle_folder", "assign_language", "assign_subtitle_type"])
  })
})

describe("the value of a folder named with its language", () => {
  it("is the name alone without a language, both as JSON with one, and a name alone of a rule written before reads back", async () => {
    const { folderValueOf, folderValueText } = await import("./ruleVocabulary")
    expect(folderValueText({ name: "Студия", language: "" })).toBe("Студия")
    const both = folderValueText({ name: "AnimeSpace Team", language: "rus" })
    expect(JSON.parse(both)).toEqual({ name: "AnimeSpace Team", language: "rus" })
    expect(folderValueOf(both)).toEqual({ name: "AnimeSpace Team", language: "rus", type: "" })
    expect(folderValueOf("Студия")).toEqual({ name: "Студия", language: "", type: "" })
    expect(folderValueOf(null)).toEqual({ name: "", language: "", type: "" })
    // A folder of subtitles has its type too.
    const signs = folderValueText({ name: "Надписи", language: "", type: "signs" })
    expect(JSON.parse(signs)).toEqual({ name: "Надписи", type: "signs" })
    expect(folderValueOf(signs)).toEqual({ name: "Надписи", language: "", type: "signs" })
  })
})
