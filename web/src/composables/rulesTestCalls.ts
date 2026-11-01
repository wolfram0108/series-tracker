import { api } from "../api/client"
import type { components } from "../api/schema"
import { call, type Answer, type CallWords } from "./sourceCalls"

type Schemas = components["schemas"]

/** The body of the test of a profile: a page to read, or lines the window holds (typed, or the names of a release). */
export type RulesTestBody = Schemas["RulesTestRequest"]
export type RulesTestReply = Schemas["CompositionPreviewReply"]

const TEST_WORDS: CallWords = { refused: "rulesTest.error.refused", network: "rulesTest.error.network" }

export function testProfile(body: RulesTestBody): Promise<Answer<RulesTestReply>> {
  return call(() => api.POST("/api/parser-profiles/test", { body }), TEST_WORDS)
}

/** The file names of the torrents of a series, for the test of a profile on a torrent series. */
export function fetchFilenames(seriesId: number): Promise<Answer<string[]>> {
  return call(() => api.GET("/api/series/{series_id}/source-filenames", { params: { path: { series_id: seriesId } } }), TEST_WORDS)
}
