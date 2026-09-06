import { describe, expect, it } from "vitest"

import {
  buildMessageParts,
  contentToEditableDraft,
  detectAttachmentKind,
  genericFileToPart,
  imageFileToPart,
  validateAttachment,
  validateAttachments,
} from "@kohakuterrarium/chat-ui"

function file(name, type, content) {
  return new File([content], name, { type })
}

describe("public chat attachment helpers", () => {
  it("converts an image to a named data URL part", async () => {
    const part = await imageFileToPart(file("cat.png", "image/png", "pixels"))
    expect(part).toMatchObject({
      type: "image_url",
      image_url: { detail: "low" },
      meta: { source_type: "attachment", source_name: "cat.png" },
    })
    expect(part.image_url.url).toMatch(/^data:image\/png;base64,/)
  })

  it("converts UTF-8 text and binary files", async () => {
    expect(await genericFileToPart(file("readme.md", "", "héllo"))).toMatchObject({
      file: {
        content: "héllo",
        data_base64: null,
        encoding: "utf-8",
        mime: "application/octet-stream",
      },
    })
    expect(
      await genericFileToPart(file("data.bin", "application/octet-stream", "abc")),
    ).toMatchObject({
      file: { content: null, data_base64: "YWJj", encoding: "base64" },
    })
  })

  it("detects type and validates kind and size limits", () => {
    expect(detectAttachmentKind(file("x.png", "image/png", "x"))).toBe("image")
    expect(detectAttachmentKind(file("x.txt", "text/plain", "x"))).toBe("file")
    expect(
      validateAttachment({ name: "x.png", type: "text/plain", size: 1 }, "image"),
    ).toMatchObject({ code: "not-image" })
    expect(
      validateAttachment({ name: "x.bin", type: "", size: 11 }, "file", { maxAttachmentBytes: 10 }),
    ).toMatchObject({ code: "too-large", limit: 10 })
    expect(validateAttachment({ name: "x.png", type: "image/png", size: 1 }, "image")).toBeNull()
  })

  it("aggregates accepted attachments and error surfaces", () => {
    const result = validateAttachments(
      [
        { name: "ok.txt", type: "text/plain", size: 2 },
        { name: "huge.png", type: "image/png", size: 20 },
      ],
      { maxImageBytes: 10 },
    )
    expect(result.attachments).toEqual([{ file: expect.anything(), name: "ok.txt", kind: "file" }])
    expect(result.errors).toEqual([
      expect.objectContaining({ code: "too-large", name: "huge.png" }),
    ])
  })

  it("builds aggregate parts and preserves editable existing parts", async () => {
    const parts = await buildMessageParts(" hello ", [
      { file: file("a.txt", "text/plain", "A"), name: "a.txt", kind: "file" },
    ])
    expect(parts).toEqual([
      { type: "text", text: " hello " },
      expect.objectContaining({ type: "file", file: expect.objectContaining({ content: "A" }) }),
    ])
    expect(contentToEditableDraft(parts)).toMatchObject({
      text: " hello ",
      attachments: [{ name: "a.txt", kind: "file" }],
    })
  })
})
