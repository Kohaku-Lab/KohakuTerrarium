import { beforeEach, describe, expect, it } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import { useCanvasStore } from "./canvas.js"

beforeEach(() => {
  setActivePinia(createPinia())
})

describe("canvas store — artifact detection", () => {
  it("picks up explicit ##canvas## markers with name + lang", () => {
    const store = useCanvasStore()
    const msg = {
      id: "m1",
      role: "assistant",
      parts: [
        {
          type: "text",
          content:
            "Here is the file:\n##canvas name=hello lang=py##\nprint('hi')\n##canvas##\nDone.",
        },
      ],
    }
    store.scanMessage(msg)
    expect(store.artifacts).toHaveLength(1)
    const a = store.artifacts[0]
    expect(a.type).toBe("code")
    expect(a.lang).toBe("py")
    expect(a.content).toContain("print('hi')")
  })

  it("detects long fenced code blocks as artifacts", () => {
    const store = useCanvasStore()
    const body = Array.from({ length: 20 }, (_, i) => `line ${i}`).join("\n")
    const msg = {
      id: "m2",
      role: "assistant",
      parts: [{ type: "text", content: "See below:\n```python\n" + body + "\n```\n" }],
    }
    store.scanMessage(msg)
    expect(store.artifacts).toHaveLength(1)
    expect(store.artifacts[0].lang).toBe("python")
  })

  it("ignores short fenced code blocks", () => {
    const store = useCanvasStore()
    const msg = {
      id: "m3",
      role: "assistant",
      parts: [{ type: "text", content: "Here:\n```js\nlet x = 1;\n```" }],
    }
    store.scanMessage(msg)
    expect(store.artifacts).toHaveLength(0)
  })

  it("updates content on re-scan with changed content", () => {
    const store = useCanvasStore()
    const art = store.upsertArtifact({ sourceId: "abc", content: "v1 body", lang: "js" })
    store.upsertArtifact({ sourceId: "abc", content: "v2 body", lang: "js" })
    expect(store.artifacts).toHaveLength(1)
    expect(store.artifacts[0].id).toBe(art.id)
    expect(store.artifacts[0].content).toBe("v2 body")
    expect(store.artifacts[0].name).toBe("v2 body")
    expect(store.activeId).toBe(art.id)
  })

  it("auto-activates newly detected artifacts after an activation catch-up scan", () => {
    const store = useCanvasStore()
    const oldMsg = {
      id: "old",
      role: "assistant",
      parts: [{ type: "text", content: "##canvas name=old lang=py##\nprint('old')\n##canvas##" }],
    }
    const newMsg = {
      id: "new",
      role: "assistant",
      parts: [{ type: "text", content: "##canvas name=new lang=py##\nprint('new')\n##canvas##" }],
    }

    store.scanMessage(oldMsg)
    const oldActive = store.activeId
    store.scanMessage(newMsg)

    expect(store.artifacts).toHaveLength(2)
    expect(store.activeId).not.toBe(oldActive)
    expect(store.activeArtifact.content).toContain("print('new')")
  })

  it("skips upsert when content is identical", () => {
    const store = useCanvasStore()
    store.upsertArtifact({ sourceId: "abc", content: "same", lang: "js" })
    store.upsertArtifact({ sourceId: "abc", content: "same", lang: "js" })
    expect(store.artifacts).toHaveLength(1)
  })

  it("picks up assistant image_url parts as image artifacts", () => {
    const store = useCanvasStore()
    const msg = {
      id: "m4",
      role: "assistant",
      parts: [
        { type: "text", content: "Here you go:" },
        {
          type: "image_url",
          image_url: { url: "data:image/png;base64,iVBORw0KGgo=", detail: "auto" },
          meta: { revised_prompt: "A cat", output_format: "png" },
        },
      ],
    }
    store.scanMessage(msg)
    expect(store.artifacts).toHaveLength(1)
    const a = store.artifacts[0]
    expect(a.type).toBe("image")
    expect(a.lang).toBe("png")
    expect(a.name).toContain("A cat")
    expect(a.content).toMatch(/^data:image\/png;base64,/)
  })

  it("infers image format from a data URL when meta is missing", () => {
    const store = useCanvasStore()
    const msg = {
      id: "m5",
      role: "assistant",
      parts: [
        {
          type: "image_url",
          image_url: { url: "data:image/webp;base64,Rg==" },
        },
      ],
    }
    store.scanMessage(msg)
    expect(store.artifacts).toHaveLength(1)
    expect(store.artifacts[0].lang).toBe("webp")
  })

  it("skips non-assistant messages", () => {
    const store = useCanvasStore()
    const bigBody = Array.from({ length: 20 }).fill("x").join("\n")
    store.scanMessage({
      id: "u1",
      role: "user",
      content: "```py\n" + bigBody + "\n```",
    })
    expect(store.artifacts).toHaveLength(0)
  })

  it("isolates artifacts by Pinia scope (one canvas instance per attach)", () => {
    // Pre-refactor the singleton canvas store kept ``byScope`` maps
    // internally; now each Pinia scope gets its own canvas instance.
    // Two scopes scanning the SAME message produce independent
    // artifact lists.
    const root = useCanvasStore("i1::root")
    const worker = useCanvasStore("i1::worker")
    const msg = {
      id: "m6",
      role: "assistant",
      parts: [{ type: "text", content: "##canvas name=one lang=py##\nprint('a')\n##canvas##" }],
    }

    root.scanMessage(msg)
    expect(root.artifacts).toHaveLength(1)
    expect(worker.artifacts).toHaveLength(0)

    worker.scanMessage(msg)
    expect(worker.artifacts).toHaveLength(1)
    expect(root.artifacts).toHaveLength(1)

    // Mutating one scope leaves the other untouched.
    root.dismiss()
    expect(root.dismissed).toBe(true)
    expect(worker.dismissed).toBe(false)
  })
})
