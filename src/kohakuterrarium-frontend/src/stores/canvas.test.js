import { beforeEach, describe, expect, it } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import { MAX_CANVAS_ARTIFACTS, useCanvasStore } from "./canvas.js"

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

  it("does not steal selection when an older artifact's content refreshes", () => {
    const store = useCanvasStore()
    const first = store.upsertArtifact({ sourceId: "old", content: "file://a.png", type: "image" })
    const second = store.upsertArtifact({ sourceId: "new", content: "file://b.png", type: "image" })
    expect(store.activeId).toBe(second.id)
    store.upsertArtifact({ sourceId: "old", content: "/api/files/raw?path=a.png", type: "image" })
    expect(store.activeId).toBe(second.id)
    expect(first.content).toBe("/api/files/raw?path=a.png")
  })

  it("a new publish becomes active even after the user clicked an older tab", () => {
    const store = useCanvasStore()
    const first = store.upsertArtifact({ sourceId: "a", content: "one", lang: "js" })
    store.upsertArtifact({ sourceId: "b", content: "two", lang: "js" })
    store.setActive(first.id)
    const third = store.upsertArtifact({ sourceId: "c", content: "three", lang: "js" })
    expect(store.activeId).toBe(third.id)
    expect(store.artifacts).toHaveLength(3)
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

describe("canvas store — write / edit canvas_preview (Feat 1)", () => {
  it("picks up write tool result canvas_preview as a code artifact", () => {
    const store = useCanvasStore()
    const msg = {
      id: "m_write",
      role: "assistant",
      parts: [
        {
          type: "tool",
          name: "write",
          status: "done",
          resultMeta: {
            canvas_preview: {
              kind: "write",
              file_path: "/repo/foo.py",
              lang: "python",
              content: "def hello():\n    return 1\n",
              bytes: 27,
              truncated: false,
            },
          },
        },
      ],
    }
    store.scanMessage(msg)
    expect(store.artifacts).toHaveLength(1)
    const a = store.artifacts[0]
    expect(a.type).toBe("code")
    expect(a.lang).toBe("python")
    expect(a.content).toBe("def hello():\n    return 1\n")
    expect(a.sourceId).toBe("file:/repo/foo.py")
  })

  it("re-editing the same file updates the existing artifact in place", () => {
    // Two write/edit calls to the same path produce ONE artifact —
    // the canvas tracks the file, not the tool call. The sourceId is
    // ``file:<path>`` precisely so the upsert finds the previous entry.
    const store = useCanvasStore()
    const make = (id, content) => ({
      id,
      role: "assistant",
      parts: [
        {
          type: "tool",
          name: "edit",
          resultMeta: {
            canvas_preview: {
              kind: "edit",
              file_path: "/repo/foo.py",
              lang: "python",
              content,
              bytes: content.length,
              truncated: false,
            },
          },
        },
      ],
    })
    store.scanMessage(make("m1", "v1"))
    store.scanMessage(make("m2", "v2"))
    expect(store.artifacts).toHaveLength(1)
    expect(store.artifacts[0].content).toBe("v2")
    // Active artifact follows the most recent touch — keeps the canvas
    // panel on the file the agent just changed.
    expect(store.activeArtifact?.content).toBe("v2")
  })

  it("skips tool parts whose preview is truncated (content === null)", () => {
    // Files over PREVIEW_MAX_BYTES surface ``content: null, truncated:
    // true``. Showing an empty code bubble would be misleading —
    // better to skip and let the FE offer a "fetch full content" stub.
    const store = useCanvasStore()
    const msg = {
      id: "m_huge",
      role: "assistant",
      parts: [
        {
          type: "tool",
          name: "write",
          resultMeta: {
            canvas_preview: {
              kind: "write",
              file_path: "/repo/huge.bin",
              lang: "text",
              content: null,
              bytes: 999999,
              truncated: true,
            },
          },
        },
      ],
    }
    store.scanMessage(msg)
    expect(store.artifacts).toHaveLength(0)
  })

  it("tool parts without canvas_preview are ignored", () => {
    // Regression: non-file tools (bash, glob, etc.) must NOT spawn
    // canvas artifacts. The detector only fires when the tool result
    // carries a ``canvas_preview`` dict.
    const store = useCanvasStore()
    const msg = {
      id: "m_bash",
      role: "assistant",
      parts: [{ type: "tool", name: "bash", resultMeta: { other: "data" }, result: "ok" }],
    }
    store.scanMessage(msg)
    expect(store.artifacts).toHaveLength(0)
  })
})

describe("canvas store — canvas_image preview", () => {
  it("picks up kind image as an image artifact, not code", () => {
    const store = useCanvasStore()
    const msg = {
      id: "m_img",
      role: "assistant",
      parts: [
        {
          type: "tool",
          name: "canvas_image",
          status: "done",
          resultMeta: {
            canvas_preview: {
              kind: "image",
              file_path: "/Users/me/out.png",
              lang: "png",
              content: "file:///Users/me/out.png",
              bytes: 1200,
              truncated: false,
            },
          },
        },
      ],
    }
    store.scanMessage(msg)
    expect(store.artifacts).toHaveLength(1)
    const a = store.artifacts[0]
    expect(a.type).toBe("image")
    expect(a.lang).toBe("png")
    expect(a.sourceId).toBe("file:/Users/me/out.png")
    expect(a.content).toBe(`/api/files/raw?path=${encodeURIComponent("/Users/me/out.png")}`)
  })

  it("keeps a session artifact URL as the image content", () => {
    const store = useCanvasStore()
    const url = "/api/sessions/abc/artifacts/canvas_images/shot.png"
    const msg = {
      id: "m_art",
      role: "assistant",
      parts: [
        {
          type: "tool",
          name: "canvas_image",
          resultMeta: {
            canvas_preview: {
              kind: "image",
              file_path: "/work/shot.png",
              lang: "png",
              content: url,
              bytes: 80,
              truncated: false,
            },
          },
        },
      ],
    }
    store.scanMessage(msg)
    expect(store.artifacts[0].type).toBe("image")
    expect(store.artifacts[0].content).toBe(url)
  })

  it("re-promoting the same path updates the existing image", () => {
    const store = useCanvasStore()
    const make = (id, content) => ({
      id,
      role: "assistant",
      parts: [
        {
          type: "tool",
          name: "canvas_image",
          resultMeta: {
            canvas_preview: {
              kind: "image",
              file_path: "/work/out.png",
              lang: "png",
              content,
              bytes: 10,
              truncated: false,
            },
          },
        },
      ],
    })
    store.scanMessage(make("m1", "/api/sessions/s/artifacts/canvas_images/a.png"))
    store.scanMessage(make("m2", "/api/sessions/s/artifacts/canvas_images/b.png"))
    expect(store.artifacts).toHaveLength(1)
    expect(store.artifacts[0].content).toBe("/api/sessions/s/artifacts/canvas_images/b.png")
    expect(store.activeArtifact?.content).toBe("/api/sessions/s/artifacts/canvas_images/b.png")
  })
})

describe("canvas store — dismiss and cap", () => {
  it("dismissArtifact removes a tile and a later upsert of that source stays gone", () => {
    const store = useCanvasStore()
    const first = store.upsertArtifact({ sourceId: "keep", content: "a", lang: "js" })
    const gone = store.upsertArtifact({ sourceId: "drop", content: "b", lang: "js" })
    store.dismissArtifact(gone.id)
    expect(store.artifacts.map((a) => a.sourceId)).toEqual(["keep"])
    expect(store.activeId).toBe(first.id)
    store.upsertArtifact({ sourceId: "drop", content: "b", lang: "js" })
    expect(store.artifacts.map((a) => a.sourceId)).toEqual(["keep"])
  })

  it("an edit of a dismissed path puts the tile back and selects it", () => {
    const store = useCanvasStore()
    const gone = store.upsertArtifact({ sourceId: "file:/work/a.py", content: "v1", lang: "py" })
    store.dismissArtifact(gone.id)
    store.upsertArtifact({ sourceId: "file:/work/a.py", content: "v1", lang: "py" })
    expect(store.artifacts).toHaveLength(0)
    const back = store.upsertArtifact({ sourceId: "file:/work/a.py", content: "v2", lang: "py" })
    expect(store.artifacts).toHaveLength(1)
    expect(store.artifacts[0].content).toBe("v2")
    expect(store.activeId).toBe(back.id)
  })

  it("rescan of an older version does not reopen a dismissed path", () => {
    const store = useCanvasStore()
    store.upsertArtifact({ sourceId: "file:/work/a.py", content: "v1", lang: "py" })
    const latest = store.upsertArtifact({ sourceId: "file:/work/a.py", content: "v2", lang: "py" })
    store.dismissArtifact(latest.id)
    store.upsertArtifact({ sourceId: "file:/work/a.py", content: "v1", lang: "py" })
    store.upsertArtifact({ sourceId: "file:/work/a.py", content: "v2", lang: "py" })
    expect(store.artifacts).toHaveLength(0)
  })

  it("scanMessage does not restore a dismissed file preview", () => {
    const store = useCanvasStore()
    const msg = {
      id: "m_drop",
      role: "assistant",
      parts: [
        {
          type: "tool",
          name: "canvas_image",
          resultMeta: {
            canvas_preview: {
              kind: "image",
              file_path: "/work/gone.png",
              lang: "png",
              content: "/api/files/raw?path=gone.png",
              bytes: 10,
              truncated: false,
            },
          },
        },
      ],
    }
    store.scanMessage(msg)
    expect(store.artifacts).toHaveLength(1)
    store.dismissArtifact(store.artifacts[0].id)
    store.scanMessage(msg)
    expect(store.artifacts).toHaveLength(0)
  })

  it("clearArtifacts hides every current tile from later scans", () => {
    const store = useCanvasStore()
    store.upsertArtifact({ sourceId: "a", content: "1", lang: "js" })
    store.upsertArtifact({ sourceId: "b", content: "2", lang: "js" })
    store.clearArtifacts()
    expect(store.artifacts).toHaveLength(0)
    expect(store.activeId).toBeNull()
    store.upsertArtifact({ sourceId: "a", content: "1", lang: "js" })
    expect(store.artifacts).toHaveLength(0)
    const back = store.upsertArtifact({ sourceId: "a", content: "1-edited", lang: "js" })
    expect(store.artifacts.map((a) => a.sourceId)).toEqual(["a"])
    expect(store.activeId).toBe(back.id)
  })

  it("evicts the oldest tile once the cap is exceeded", () => {
    const store = useCanvasStore()
    for (let i = 0; i < MAX_CANVAS_ARTIFACTS + 1; i++) {
      store.upsertArtifact({ sourceId: `n${i}`, content: String(i), lang: "js" })
    }
    expect(store.artifacts).toHaveLength(MAX_CANVAS_ARTIFACTS)
    expect(store.artifacts[0].sourceId).toBe("n1")
    expect(store.artifacts.at(-1).sourceId).toBe(`n${MAX_CANVAS_ARTIFACTS}`)
    store.upsertArtifact({ sourceId: "n0", content: "0", lang: "js" })
    expect(store.artifacts.some((a) => a.sourceId === "n0")).toBe(false)
    store.upsertArtifact({ sourceId: "n0", content: "again", lang: "js" })
    expect(store.artifacts.at(-1).sourceId).toBe("n0")
    expect(store.artifacts).toHaveLength(MAX_CANVAS_ARTIFACTS)
    expect(store.artifacts.some((a) => a.sourceId === "n1")).toBe(false)
  })

  it("scanMessage reopens a dismissed file after a later edit preview", () => {
    const store = useCanvasStore()
    const preview = (id, content) => ({
      id,
      role: "assistant",
      parts: [
        {
          type: "tool",
          name: "edit",
          resultMeta: {
            canvas_preview: {
              kind: "edit",
              file_path: "/work/a.py",
              lang: "py",
              content,
              bytes: content.length,
              truncated: false,
            },
          },
        },
      ],
    })
    store.scanMessage(preview("m1", "v1"))
    store.dismissArtifact(store.artifacts[0].id)
    store.scanMessage(preview("m1", "v1"))
    expect(store.artifacts).toHaveLength(0)
    store.scanMessage(preview("m2", "v2"))
    expect(store.artifacts).toHaveLength(1)
    expect(store.artifacts[0].content).toBe("v2")
  })
})
