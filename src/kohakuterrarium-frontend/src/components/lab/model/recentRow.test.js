import { describe, expect, it } from "vitest"

import { historyRow } from "@/components/shell/history/historyRows"

import { recentHeadline, recentTitle } from "./recentRow"

describe("recent row", () => {
  it("leads with the summary and names the session only by a title the user gave", () => {
    const titled = historyRow({ name: "s_1", title: "Nightly", summary: "Triaging CI" })
    expect([recentHeadline(titled), recentTitle(titled)]).toEqual(["Triaging CI", "Nightly"])
    const untitled = historyRow({ name: "s_2", terrarium_name: "swe_team", summary: "Triaging CI" })
    expect([recentHeadline(untitled), recentTitle(untitled)]).toEqual(["Triaging CI", ""])
  })

  it("falls back to the label without a summary, never to a prompt", () => {
    const row = historyRow({ name: "s_3", title: "Nightly", last_user: "Why is CI red?" })
    expect([recentHeadline(row), recentTitle(row)]).toEqual(["Nightly", ""])
    const bare = historyRow({ name: "s_4", terrarium_name: "swe_team", last_user: "Hi" })
    expect(recentHeadline(bare)).toBe("swe_team")
  })
})
