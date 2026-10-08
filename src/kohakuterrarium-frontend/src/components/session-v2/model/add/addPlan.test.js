import { describe, expect, it } from "vitest"

import {
  NEW,
  buildAddPlan,
  emptyAddForm,
  isValidName,
  suggestName,
  toggleIn,
  usedNewChannels,
  validateAddForm,
} from "./addPlan"

const ctx = { creatures: ["root", "swe"], channels: ["tasks", "team"] }
const form = (kind, patch = {}) => ({ ...emptyAddForm(kind), ...patch })

describe("validateAddForm", () => {
  it("accepts a complete creature form and flags each missing or bad field", () => {
    expect(validateAddForm(form("creature", { name: "rev", configPath: "@pkg/rev" }), ctx)).toEqual(
      {},
    )
    expect(validateAddForm(form("creature"), ctx)).toEqual({
      name: "add.err.nameRequired",
      config: "add.err.configRequired",
    })
    expect(validateAddForm(form("creature", { name: "rev", configPath: "   " }), ctx).config).toBe(
      "add.err.configRequired",
    )
    expect(validateAddForm(form("creature", { name: "swe", configPath: "p" }), ctx).name).toBe(
      "add.err.nameTaken",
    )
    expect(
      validateAddForm(form("creature", { name: "-bad name", configPath: "p" }), ctx).name,
    ).toBe("add.err.nameFormat")
    expect(validateAddForm(form("inline", { name: "x", configYaml: "  " }), ctx).config).toBe(
      "add.err.yamlRequired",
    )
  })

  it("validates only new channels the creature uses, so an unused one cannot lock the form", () => {
    const base = { name: "rev", configPath: "p" }
    expect(
      validateAddForm(form("creature", { ...base, newChannels: ["team"], listen: ["team"] }), ctx)
        .newChannels,
    ).toBe("add.err.channelName")
    expect(validateAddForm(form("creature", { ...base, newChannels: ["bad name"] }), ctx)).toEqual(
      {},
    )
    expect(
      validateAddForm(
        form("creature", { ...base, newChannels: ["bad name"], send: ["bad name"] }),
        ctx,
      ).newChannels,
    ).toBe("add.err.channelName")
  })

  it("flags output targets and members no longer in the session", () => {
    const base = { name: "rev", configPath: "p" }
    expect(validateAddForm(form("creature", { ...base, outputTo: "ghost" }), ctx).outputTo).toBe(
      "add.err.unknownTarget",
    )
    expect(
      validateAddForm(form("creature", { ...base, inputsFrom: ["ghost"] }), ctx).inputsFrom,
    ).toBe("add.err.unknownTarget")
    expect(validateAddForm(form("channel", { name: "x", listeners: ["ghost"] }), ctx).members).toBe(
      "add.err.unknownTarget",
    )
    expect(validateAddForm(form("creature", { ...base, send: ["gone"] }), ctx).newChannels).toBe(
      "add.err.unknownChannel",
    )
  })

  it("validates the terrarium and channel kinds on their own fields", () => {
    expect(validateAddForm(form("terrarium"), ctx)).toEqual({ config: "add.err.recipeRequired" })
    expect(validateAddForm(form("channel", { name: "tasks" }), ctx)).toEqual({
      name: "add.err.channelTaken",
    })
    expect(validateAddForm(form("channel", { name: "findings" }), ctx)).toEqual({})
  })
})

describe("buildAddPlan", () => {
  it("adds the creature, creates used new channels, then wires channels and outputs to it", () => {
    const steps = buildAddPlan(
      form("creature", {
        name: " rev ",
        configPath: " @pkg/rev ",
        newChannels: ["findings", "unused"],
        listen: ["tasks", "findings"],
        send: ["team"],
        outputTo: "root",
        inputsFrom: ["swe"],
      }),
    )
    expect(steps).toEqual([
      { op: "addCreature", body: { name: "rev", config_path: "@pkg/rev" } },
      { op: "addChannel", name: "findings", description: "" },
      { op: "wire", creature: NEW, channel: "tasks", direction: "listen" },
      { op: "wire", creature: NEW, channel: "findings", direction: "listen" },
      { op: "wire", creature: NEW, channel: "team", direction: "send" },
      { op: "addOutput", from: NEW, to: "root" },
      { op: "addOutput", from: "swe", to: NEW },
    ])
    expect(JSON.stringify(steps)).not.toMatch(/privileged|start/)
  })

  it("sends inline YAML instead of a path for the inline kind", () => {
    const [step] = buildAddPlan(form("inline", { name: "x", configYaml: "system_prompt: hi" }))
    expect(step).toEqual({
      op: "addCreature",
      body: { name: "x", config_yaml: "system_prompt: hi" },
    })
  })

  it("merges a recipe, or creates a channel and wires its members", () => {
    expect(buildAddPlan(form("terrarium", { recipePath: " /r " }))).toEqual([
      { op: "applyRecipe", configPath: "/r" },
    ])
    expect(
      buildAddPlan(
        form("channel", {
          name: "findings",
          description: " notes ",
          listeners: ["root"],
          senders: ["swe"],
        }),
      ),
    ).toEqual([
      { op: "addChannel", name: "findings", description: "notes" },
      { op: "wire", creature: "root", channel: "findings", direction: "listen" },
      { op: "wire", creature: "swe", channel: "findings", direction: "send" },
    ])
  })
})

describe("helpers", () => {
  it("suggests a valid unused name from a config name, path or file name", () => {
    expect(suggestName("reviewer", [])).toBe("reviewer")
    expect(suggestName("swe", ["swe", "swe-2"])).toBe("swe-3")
    expect(suggestName("@kt-biome/creatures/my agent", [])).toBe("my-agent")
    expect(suggestName("C:\\work\\creatures\\critic\\", [])).toBe("critic")
    expect(suggestName("reviewer.yaml", [])).toBe("reviewer")
    expect(suggestName("", [])).toBe("creature")
  })

  it("checks names, lists used new channels, and toggles without mutating", () => {
    expect([
      isValidName("a.b-c_1"),
      isValidName("-x"),
      isValidName("a b"),
      isValidName(""),
    ]).toEqual([true, false, false, false])
    expect(usedNewChannels({ newChannels: ["a", "b"], listen: ["b"], send: [] })).toEqual(["b"])
    const list = ["a"]
    expect(toggleIn(list, "b")).toEqual(["a", "b"])
    expect(toggleIn(list, "a")).toEqual([])
    expect(list).toEqual(["a"])
  })
})
