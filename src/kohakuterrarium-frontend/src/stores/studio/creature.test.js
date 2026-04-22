/**
 * Creature store — draft / dirty / add/remove module / save flow.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

vi.mock("@/utils/studio/api", () => ({
  creatureAPI: {
    load: vi.fn(),
    save: vi.fn(),
  },
  validateAPI: {
    creature: vi.fn(),
  },
}))

import { creatureAPI, validateAPI } from "@/utils/studio/api"
import { useStudioCreatureStore } from "./creature.js"

const FIXTURE = {
  name: "alpha",
  path: "/tmp/ws/creatures/alpha",
  config: {
    name: "alpha",
    version: "1.0",
    description: "a test",
    base_config: "@kt-biome/creatures/general",
    system_prompt_file: "prompts/system.md",
    tools: [
      { name: "bash", type: "builtin" },
      { name: "read", type: "builtin" },
      { name: "add_timer", type: "trigger" },
    ],
    subagents: [{ name: "explore", type: "builtin" }],
  },
  prompts: { "prompts/system.md": "# alpha\n" },
  effective: {
    model: "gpt-5.4",
    tools: ["bash", "read"],
    subagents: ["explore"],
    inheritance_chain: ["@kt-biome/creatures/general"],
  },
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

describe("creature store — load + derived", () => {
  it("load() populates saved + draft", async () => {
    creatureAPI.load.mockResolvedValueOnce(FIXTURE)
    const s = useStudioCreatureStore()
    await s.load("alpha")
    expect(s.saved.config.name).toBe("alpha")
    expect(s.draft.config.name).toBe("alpha")
    expect(s.dirty).toBe(false)
  })

  it("draft mutations do not alter saved", async () => {
    creatureAPI.load.mockResolvedValueOnce(FIXTURE)
    const s = useStudioCreatureStore()
    await s.load("alpha")
    s.patch("description", "edited")
    expect(s.draft.config.description).toBe("edited")
    expect(s.saved.config.description).toBe("a test")
    expect(s.dirty).toBe(true)
  })

  it("systemPromptText reads through the draft", async () => {
    creatureAPI.load.mockResolvedValueOnce(FIXTURE)
    const s = useStudioCreatureStore()
    await s.load("alpha")
    expect(s.systemPromptText).toBe("# alpha\n")
    s.setPromptFile("prompts/system.md", "# patched")
    expect(s.systemPromptText).toBe("# patched")
  })

  it("patch writes nested paths, auto-creating ancestors", async () => {
    creatureAPI.load.mockResolvedValueOnce(FIXTURE)
    const s = useStudioCreatureStore()
    await s.load("alpha")
    s.patch("memory.embedding.provider", "model2vec")
    s.patch("memory.embedding.model", "@retrieval")
    expect(s.draft.config.memory.embedding.provider).toBe("model2vec")
    expect(s.draft.config.memory.embedding.model).toBe("@retrieval")
  })

  it("patch(undefined) on a nested leaf cascade-deletes empty ancestors", async () => {
    creatureAPI.load.mockResolvedValueOnce(FIXTURE)
    const s = useStudioCreatureStore()
    await s.load("alpha")
    // Start with a nested memory config
    s.patch("memory.embedding.provider", "model2vec")
    expect(s.draft.config.memory?.embedding?.provider).toBe("model2vec")
    // Clearing the only leaf should drop embedding AND memory entirely
    s.patch("memory.embedding.provider", undefined)
    expect(s.draft.config.memory).toBeUndefined()
  })

  it("cascade-delete stops at the first non-empty ancestor", async () => {
    creatureAPI.load.mockResolvedValueOnce(FIXTURE)
    const s = useStudioCreatureStore()
    await s.load("alpha")
    s.patch("memory.embedding.provider", "model2vec")
    s.patch("memory.other_setting", "keep")
    s.patch("memory.embedding.provider", undefined)
    // `memory.embedding` was empty after provider went → dropped.
    // `memory` still has other_setting → kept.
    expect(s.draft.config.memory).toEqual({ other_setting: "keep" })
  })
})

describe("creature store — addModule / removeModule", () => {
  beforeEach(async () => {
    creatureAPI.load.mockResolvedValueOnce(FIXTURE)
  })

  it("adds a tool", async () => {
    const s = useStudioCreatureStore()
    await s.load("alpha")
    s.addModule("tool", "write")
    expect(s.tools.some((t) => t.name === "write" && t.type === "builtin")).toBe(true)
    expect(s.dirty).toBe(true)
  })

  it("adds a trigger into the tools list with type=trigger", async () => {
    const s = useStudioCreatureStore()
    await s.load("alpha")
    s.addModule("trigger", "add_schedule")
    const entry = s.tools.find((t) => t.name === "add_schedule")
    expect(entry).toBeTruthy()
    expect(entry.type).toBe("trigger")
  })

  it("dedupes adds — same kind + name is a no-op", async () => {
    const s = useStudioCreatureStore()
    await s.load("alpha")
    s.addModule("tool", "bash") // already present
    const count = s.tools.filter((t) => t.name === "bash").length
    expect(count).toBe(1)
  })

  it("distinguishes tool vs trigger when removing same-named", async () => {
    const s = useStudioCreatureStore()
    await s.load("alpha")
    // Fixture has `add_timer` trigger. Add a tool also named add_timer.
    s.addModule("tool", "add_timer")
    expect(s.tools.filter((t) => t.name === "add_timer").length).toBe(2)
    // Removing the tool variant only drops the builtin one.
    s.removeModule("tool", "add_timer")
    const remaining = s.tools.filter((t) => t.name === "add_timer")
    expect(remaining.length).toBe(1)
    expect(remaining[0].type).toBe("trigger")
  })

  it("removes a sub-agent", async () => {
    const s = useStudioCreatureStore()
    await s.load("alpha")
    s.removeModule("subagent", "explore")
    expect(s.subagents.some((x) => x.name === "explore")).toBe(false)
  })

  it("isWired reflects draft state", async () => {
    const s = useStudioCreatureStore()
    await s.load("alpha")
    expect(s.isWired("tool", "bash")).toBe(true)
    expect(s.isWired("tool", "write")).toBe(false)
    expect(s.isWired("trigger", "add_timer")).toBe(true)
  })

  it("toggleModule adds when absent and removes when present", async () => {
    const s = useStudioCreatureStore()
    await s.load("alpha")
    expect(s.toggleModule("tool", "write")).toBe("added")
    expect(s.isWired("tool", "write")).toBe(true)
    expect(s.toggleModule("tool", "write")).toBe("removed")
    expect(s.isWired("tool", "write")).toBe(false)
  })
})

describe("creature store — save / discard", () => {
  beforeEach(async () => {
    creatureAPI.load.mockResolvedValueOnce(FIXTURE)
  })

  it("save() validates then PUTs + updates saved", async () => {
    const s = useStudioCreatureStore()
    await s.load("alpha")
    s.patch("description", "edited")

    const freshCopy = {
      ...FIXTURE,
      config: { ...FIXTURE.config, description: "edited" },
    }
    validateAPI.creature.mockResolvedValueOnce({ ok: true, errors: [] })
    creatureAPI.save.mockResolvedValueOnce(freshCopy)

    const r = await s.save()
    expect(r.ok).toBe(true)
    expect(validateAPI.creature).toHaveBeenCalledOnce()
    expect(creatureAPI.save).toHaveBeenCalledOnce()
    expect(s.saved.config.description).toBe("edited")
    expect(s.dirty).toBe(false)
  })

  it("save() surfaces validation errors and does not PUT", async () => {
    const s = useStudioCreatureStore()
    await s.load("alpha")
    s.addModule("tool", "bogus")

    validateAPI.creature.mockResolvedValueOnce({
      ok: false,
      errors: [{ code: "unknown_builtin_tool", field: "tools[3].name" }],
    })

    const r = await s.save()
    expect(r.ok).toBe(false)
    expect(creatureAPI.save).not.toHaveBeenCalled()
    expect(s.validationErrors.length).toBe(1)
    expect(s.dirty).toBe(true)
  })

  it("discard() resets draft to saved", async () => {
    const s = useStudioCreatureStore()
    await s.load("alpha")
    s.patch("description", "scratch")
    expect(s.dirty).toBe(true)
    s.discard()
    expect(s.dirty).toBe(false)
    expect(s.draft.config.description).toBe("a test")
  })
})

describe("creature store — local draft autosave", () => {
  it("restore local draft when it differs from saved", async () => {
    // Seed a stale local draft
    const local = JSON.parse(JSON.stringify(FIXTURE))
    local.config.description = "from localStorage"
    localStorage.setItem("studio:draft:creature:alpha", JSON.stringify(local))

    creatureAPI.load.mockResolvedValueOnce(FIXTURE)
    const s = useStudioCreatureStore()
    await s.load("alpha")
    expect(s.draft.config.description).toBe("from localStorage")
    expect(s.dirty).toBe(true)
  })

  it("ignores local draft that matches saved (cleans up)", async () => {
    const local = JSON.parse(JSON.stringify(FIXTURE))
    localStorage.setItem("studio:draft:creature:alpha", JSON.stringify(local))

    creatureAPI.load.mockResolvedValueOnce(FIXTURE)
    const s = useStudioCreatureStore()
    await s.load("alpha")
    expect(s.draft.config.description).toBe("a test")
    expect(s.dirty).toBe(false)
    expect(localStorage.getItem("studio:draft:creature:alpha")).toBeNull()
  })
})
