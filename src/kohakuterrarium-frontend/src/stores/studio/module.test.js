/**
 * Module store — load / draft / dirty / save / round-trip-fallback flow.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

vi.mock("@/utils/studio/api", () => ({
  moduleAPI: {
    load: vi.fn(),
    save: vi.fn(),
  },
}))

import { moduleAPI } from "@/utils/studio/api"
import { useStudioModuleStore } from "./module.js"

const FIXTURE = {
  kind: "tools",
  name: "my_tool",
  path: "modules/tools/my_tool.py",
  raw_source: "class MyTool(BaseTool):\n    ...\n",
  mode: "simple",
  form: {
    class_name: "MyTool",
    tool_name: "my_tool",
    description: "Do a thing.",
    execution_mode: "direct",
    needs_context: false,
    require_manual_read: false,
    params: [],
  },
  execute_body: 'return ToolResult(output="ok")',
  warnings: [],
}

function makeLocalStorageStub() {
  const store = new Map()
  return {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => {
      store.set(k, String(v))
    },
    removeItem: (k) => {
      store.delete(k)
    },
    clear: () => store.clear(),
    key: (i) => Array.from(store.keys())[i] ?? null,
    get length() {
      return store.size
    },
  }
}

beforeEach(() => {
  vi.stubGlobal("localStorage", makeLocalStorageStub())
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe("module store — load + derived", () => {
  it("load() populates saved + draft", async () => {
    moduleAPI.load.mockResolvedValueOnce(FIXTURE)
    const s = useStudioModuleStore()
    await s.load("tools", "my_tool")
    expect(s.saved.form.tool_name).toBe("my_tool")
    expect(s.draft.form.tool_name).toBe("my_tool")
    expect(s.dirty).toBe(false)
    expect(s.mode).toBe("simple")
    expect(s.executeBody).toContain("ToolResult")
    expect(s.path).toBe("modules/tools/my_tool.py")
  })

  it("patchForm mutates draft only, flips dirty", async () => {
    moduleAPI.load.mockResolvedValueOnce(FIXTURE)
    const s = useStudioModuleStore()
    await s.load("tools", "my_tool")
    s.patchForm("description", "new")
    expect(s.draft.form.description).toBe("new")
    expect(s.saved.form.description).toBe("Do a thing.")
    expect(s.dirty).toBe(true)
  })

  it("setMode + setExecuteBody + setRawSource flip dirty", async () => {
    moduleAPI.load.mockResolvedValueOnce(FIXTURE)
    const s = useStudioModuleStore()
    await s.load("tools", "my_tool")

    s.setMode("raw")
    expect(s.mode).toBe("raw")
    expect(s.dirty).toBe(true)

    s.setMode("simple")
    expect(s.dirty).toBe(false)

    s.setExecuteBody("pass")
    expect(s.executeBody).toBe("pass")
    expect(s.dirty).toBe(true)
  })

  it("patchForm with undefined deletes + cascade-cleans", async () => {
    const withOptions = {
      ...FIXTURE,
      form: { ...FIXTURE.form, options: { nested: { k: 1 } } },
    }
    moduleAPI.load.mockResolvedValueOnce(withOptions)
    const s = useStudioModuleStore()
    await s.load("tools", "my_tool")
    s.patchForm("options.nested.k", undefined)
    expect(s.draft.form.options).toBeUndefined()
  })
})

describe("module store — save + discard", () => {
  it("save() replaces saved + clears dirty", async () => {
    moduleAPI.load.mockResolvedValueOnce(FIXTURE)
    const s = useStudioModuleStore()
    await s.load("tools", "my_tool")
    s.patchForm("description", "new")

    const updated = {
      ...FIXTURE,
      form: { ...FIXTURE.form, description: "new" },
      raw_source: "# updated\n",
    }
    moduleAPI.save.mockResolvedValueOnce(updated)
    const res = await s.save()

    expect(res).toEqual({ ok: true })
    expect(moduleAPI.save).toHaveBeenCalledWith("tools", "my_tool", {
      mode: "simple",
      form: expect.objectContaining({ description: "new" }),
      execute_body: expect.any(String),
      raw_source: expect.any(String),
    })
    expect(s.saved.form.description).toBe("new")
    expect(s.dirty).toBe(false)
  })

  it("roundtrip_failed flips mode to raw and surfaces error", async () => {
    moduleAPI.load.mockResolvedValueOnce(FIXTURE)
    const s = useStudioModuleStore()
    await s.load("tools", "my_tool")
    s.patchForm("description", "breaks round-trip")

    const err = new Error("Could not round-trip")
    err.code = "roundtrip_failed"
    moduleAPI.save.mockRejectedValueOnce(err)

    const res = await s.save()
    expect(res.ok).toBe(false)
    expect(res.roundTrip).toBe(true)
    expect(s.mode).toBe("raw")
    expect(s.roundTripError).toContain("round-trip")
    expect(s.rawSource).toBe(FIXTURE.raw_source)
  })

  it("discard() resets draft to saved", async () => {
    moduleAPI.load.mockResolvedValueOnce(FIXTURE)
    const s = useStudioModuleStore()
    await s.load("tools", "my_tool")
    s.patchForm("description", "temp")
    expect(s.dirty).toBe(true)
    s.discard()
    expect(s.dirty).toBe(false)
    expect(s.draft.form.description).toBe("Do a thing.")
  })
})

describe("module store — draft persistence", () => {
  it("restores a previously-saved draft from localStorage", async () => {
    const offlineDraft = {
      ...FIXTURE,
      form: { ...FIXTURE.form, description: "offline edit" },
    }
    localStorage.setItem("studio:draft:module:tools:my_tool", JSON.stringify(offlineDraft))

    moduleAPI.load.mockResolvedValueOnce(FIXTURE)
    const s = useStudioModuleStore()
    await s.load("tools", "my_tool")

    expect(s.draft.form.description).toBe("offline edit")
    expect(s.saved.form.description).toBe("Do a thing.")
    expect(s.dirty).toBe(true)
  })

  it("drops the draft when it matches saved", async () => {
    localStorage.setItem("studio:draft:module:tools:my_tool", JSON.stringify(FIXTURE))

    moduleAPI.load.mockResolvedValueOnce(FIXTURE)
    const s = useStudioModuleStore()
    await s.load("tools", "my_tool")

    expect(s.dirty).toBe(false)
    expect(localStorage.getItem("studio:draft:module:tools:my_tool")).toBeNull()
  })
})
