import { describe, expect, it } from "vitest"

import { fileReferencePath, mediaSourceUrl, safeArtifactUrl, safeMediaParts } from "./artifacts"

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

describe("fileReferencePath", () => {
  it("decodes a local file reference", () => {
    expect(fileReferencePath("file:///home/me/a%20b.png")).toBe("/home/me/a b.png")
    expect(fileReferencePath("file:///C:/Users/me/x.png")).toBe("C:/Users/me/x.png")
  })

  it.each(["file://host/share/x.png", "https://x/y.png", "/api/sessions/s1/artifacts/a.png", null])(
    "rejects anything that is not a local file: %s",
    (value) => {
      expect(fileReferencePath(value)).toBe("")
    },
  )
})

describe("mediaSourceUrl", () => {
  it("serves artifacts from the artifact route and file references from the raw route", () => {
    expect(mediaSourceUrl("/api/sessions/s1/artifacts/a.png")).toBe(
      "/api/sessions/s1/artifacts/a.png",
    )
    expect(mediaSourceUrl("file:///tmp/a b.png")).toBe("/api/files/raw?path=%2Ftmp%2Fa%20b.png")
    expect(mediaSourceUrl("https://evil.invalid/a.png")).toBe("")
  })

  it("is idempotent over an already-resolved raw file URL", () => {
    const raw = "/api/files/raw?path=%2Ftmp%2Fa.mp4"
    expect(mediaSourceUrl(raw)).toBe(raw)
    expect(mediaSourceUrl("/api/files/raw?path=%2Ftmp%2Fa.mp4&x=1")).toBe("")
    expect(mediaSourceUrl("/api/files/raw?path=")).toBe("")
  })
})

describe("safeMediaParts", () => {
  it("keeps artifact-backed and file-referenced media only", () => {
    const image = "/api/sessions/s1/artifacts/generated_images/a.jpeg"
    const video = "/api/sessions/s1/artifacts/generated_videos/a.mp4"
    expect(
      safeMediaParts([
        { type: "image_url", image_url: { url: image } },
        { type: "file", file: { path: video, mime: "video/mp4" } },
        {
          type: "image_url",
          image_url: { url: "file:///tmp/seen.png" },
          meta: { source_type: "file" },
        },
        { type: "image_url", image_url: { url: "https://evil.invalid/a.jpeg" } },
        { type: "text", text: "details" },
      ]),
    ).toEqual([
      { type: "image_url", image_url: { url: image } },
      { type: "file", file: { path: video, mime: "video/mp4" } },
      {
        type: "image_url",
        image_url: { url: "/api/files/raw?path=%2Ftmp%2Fseen.png" },
        meta: { source_type: "file" },
      },
    ])
  })
})
