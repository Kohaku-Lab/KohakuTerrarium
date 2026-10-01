import { describe, expect, it } from "vitest"
import { savedSessionLabel } from "./sessionLabels"

describe("saved session labels", () => {
  it.each([
    [{ name: "file", terrarium_name: "Team", agents: ["Agent"] }, "Team"],
    [{ name: "file", terrarium_name: "  ", agents: ["My agent"] }, "My agent"],
    [{ name: "file", session_name: "legacy" }, "legacy"],
    [{ name: "file" }, "file"],
    [null, ""],
  ])("chooses display metadata without changing storage keys", (session, label) => {
    const before = JSON.stringify(session)
    expect(savedSessionLabel(session)).toBe(label)
    expect(JSON.stringify(session)).toBe(before)
  })
})
