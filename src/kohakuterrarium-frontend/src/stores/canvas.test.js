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
    store.upsertArtifact({ sourceId: "abc", content: "v1 body", lang: "js" })
    store.upsertArtifact({ sourceId: "abc", content: "v2 body", lang: "js" })
    expect(store.artifacts).toHaveLength(1)
    expect(store.artifacts[0].content).toBe("v2 body")
  })

  it("skips upsert when content is identical", () => {
    const store = useCanvasStore()
    store.upsertArtifact({ sourceId: "abc", content: "same", lang: "js" })
    store.upsertArtifact({ sourceId: "abc", content: "same", lang: "js" })
    expect(store.artifacts).toHaveLength(1)
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
})
