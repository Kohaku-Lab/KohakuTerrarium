import { describe, expect, it } from "vitest"

import { NEW } from "./addPlan"
import { errorText, runAddPlan } from "./runAddPlan"

function fakeClient({ failOn } = {}) {
  const calls = []
  const record =
    (name, result) =>
    async (...args) => {
      calls.push([name, ...args])
      if (failOn === name) throw { response: { data: { detail: `${name} broke` } } }
      return result
    }
  return {
    calls,
    addChannel: record("addChannel", { status: "created" }),
    addCreature: record("addCreature", { creature_id: "cid-rev" }),
    addOutput: record("addOutput", { status: "wired" }),
    wire: record("wire", { status: "wired" }),
    applyRecipe: record("applyRecipe", { creature_ids: ["c1", "c2"] }),
  }
}

describe("runAddPlan", () => {
  it("runs steps in order and substitutes the new creature's id wherever NEW appears", async () => {
    const client = fakeClient()
    const steps = [
      { op: "addChannel", name: "findings", description: "" },
      { op: "addCreature", body: { name: "rev" } },
      { op: "wire", creature: NEW, channel: "findings", direction: "listen" },
      { op: "addOutput", from: NEW, to: "root" },
      { op: "addOutput", from: "swe", to: NEW },
      { op: "wire", creature: "root", channel: "findings", direction: "send" },
    ]
    const res = await runAddPlan("sid", steps, client)
    expect(res).toEqual({ done: 6, createdId: "cid-rev", createdIds: ["cid-rev"] })
    expect(client.calls).toEqual([
      ["addChannel", "sid", "findings", ""],
      ["addCreature", "sid", { name: "rev" }],
      ["wire", "sid", "cid-rev", "findings", "listen"],
      ["addOutput", "sid", "cid-rev", "root"],
      ["addOutput", "sid", "swe", "cid-rev"],
      ["wire", "sid", "root", "findings", "send"],
    ])
  })

  it("reports the recipe's created creatures", async () => {
    const client = fakeClient()
    const res = await runAddPlan("sid", [{ op: "applyRecipe", configPath: "/r" }], client)
    expect(res.createdIds).toEqual(["c1", "c2"])
    expect(client.calls).toEqual([["applyRecipe", "sid", "/r"]])
  })

  it("resumes the remaining steps with the creature created by the failed run", async () => {
    const failing = fakeClient({ failOn: "wire" })
    const steps = [
      { op: "addCreature", body: { name: "rev" } },
      { op: "wire", creature: NEW, channel: "t", direction: "listen" },
      { op: "addOutput", from: NEW, to: "root" },
    ]
    let failure = null
    await runAddPlan("sid", steps, failing).catch((e) => (failure = e))
    expect(failure).toMatchObject({ index: 1, createdId: "cid-rev" })
    const retry = fakeClient()
    await runAddPlan("sid", steps.slice(failure.index), retry, { createdId: failure.createdId })
    expect(retry.calls).toEqual([
      ["wire", "sid", "cid-rev", "t", "listen"],
      ["addOutput", "sid", "cid-rev", "root"],
    ])
  })

  it("stops at the first failing step and says which one", async () => {
    const client = fakeClient({ failOn: "addCreature" })
    const steps = [
      { op: "addChannel", name: "findings", description: "" },
      { op: "addCreature", body: { name: "rev" } },
      { op: "addOutput", from: NEW, to: "root" },
    ]
    await expect(runAddPlan("sid", steps, client)).rejects.toMatchObject({
      index: 1,
      step: { op: "addCreature" },
    })
    expect(client.calls.map((c) => c[0])).toEqual(["addChannel", "addCreature"])
  })

  it("rejects unknown steps", async () => {
    await expect(runAddPlan("sid", [{ op: "teleport" }], fakeClient())).rejects.toMatchObject({
      index: 0,
    })
  })
})

describe("errorText", () => {
  it("reads API detail strings, pydantic detail lists, and plain errors", () => {
    expect(errorText({ response: { data: { detail: "nope" } } })).toBe("nope")
    expect(errorText({ response: { data: { detail: [{ msg: "a" }, { msg: "b" }] } } })).toBe("a; b")
    expect(errorText(new Error("boom"))).toBe("boom")
  })
})
