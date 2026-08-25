import { describe, expect, it } from "vitest"

import { safeArtifactUrl, safeMediaParts } from "./artifacts"

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

describe("safeMediaParts", () => {
  it("keeps artifact-backed images and videos only", () => {
    const image = "/api/sessions/s1/artifacts/generated_images/a.jpeg"
    const video = "/api/sessions/s1/artifacts/generated_videos/a.mp4"
    expect(
      safeMediaParts([
        { type: "image_url", image_url: { url: image } },
        { type: "file", file: { path: video, mime: "video/mp4" } },
        { type: "image_url", image_url: { url: "https://evil.invalid/a.jpeg" } },
        { type: "text", text: "details" },
      ]),
    ).toEqual([
      { type: "image_url", image_url: { url: image } },
      { type: "file", file: { path: video, mime: "video/mp4" } },
    ])
  })
})
