import { describe, expect, it } from "vitest"

import { safeArtifactUrl } from "./artifacts"

describe("safeArtifactUrl", () => {
  it("keeps canonical session artifact paths", () => {
    expect(safeArtifactUrl("/api/sessions/graph_1_deadbeef/artifacts/generated/a file.mp4")).toBe(
      "/api/sessions/graph_1_deadbeef/artifacts/generated/a%20file.mp4",
    )
  })

  it.each([
    "javascript:alert(1)",
    "//attacker.example/file.mp4",
    "https://attacker.example/file.mp4",
    "/api/sessions/s1/not-artifacts/file.mp4",
    "/api/sessions/s1/artifacts/",
    "/api\\sessions\\s1\\artifacts\\file.mp4",
  ])("rejects unsafe or unrelated paths: %s", (value) => {
    expect(safeArtifactUrl(value)).toBe("")
  })
})
