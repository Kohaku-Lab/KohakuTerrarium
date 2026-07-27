import { beforeEach, describe, expect, it, vi } from "vitest"

const preflightResume = vi.fn()
vi.mock("@/utils/api", () => ({ sessionAPI: { preflightResume } }))

import { prepareWorkspaceResume } from "./workdirPrompt"

describe("prepareWorkspaceResume", () => {
  beforeEach(() => vi.clearAllMocks())

  it("returns immediately when all saved workspaces are valid", async () => {
    preflightResume.mockResolvedValue({ ready: true, gaps: [] })
    await expect(prepareWorkspaceResume("saved")).resolves.toEqual({
      action: "resume",
      workspaceOverrides: {},
      memberWorkspaceOverrides: {},
      memberPwdOverrides: {},
      members: undefined,
      pwd: undefined,
    })
  })

  it("collects one replacement per shared invalid path group and preserves all other paths", async () => {
    preflightResume
      .mockResolvedValueOnce({
        ready: false,
        gaps: [
          { gap_id: "path:a", saved_pwd: "/gone/a", members: ["one", "two"] },
          { gap_id: "creature:b", saved_pwd: null, members: ["three"] },
        ],
      })
      .mockResolvedValueOnce({ ready: true, gaps: [] })
    const chooseWorkspace = vi
      .fn()
      .mockResolvedValueOnce({ action: "choose", path: "/new/a" })
      .mockResolvedValueOnce({ action: "choose", path: "/new/b" })

    const result = await prepareWorkspaceResume("saved", { onNode: "w1", chooseWorkspace })

    expect(result.workspaceOverrides).toEqual({ "path:a": "/new/a", "creature:b": "/new/b" })
    expect(chooseWorkspace).toHaveBeenCalledTimes(2)
    expect(chooseWorkspace).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ gap: expect.objectContaining({ onNode: "w1" }) }),
    )
    expect(preflightResume).toHaveBeenLastCalledWith("saved", {
      onNode: "w1",
      chooseWorkspace: undefined,
      members: undefined,
      workspaceOverrides: { "path:a": "/new/a", "creature:b": "/new/b" },
      memberWorkspaceOverrides: {},
      memberPwdOverrides: {},
      pwd: undefined,
    })
  })

  it("keeps identical gap ids separate across cluster members", async () => {
    preflightResume
      .mockResolvedValueOnce({
        ready: false,
        members: [
          {
            sid: "a",
            on_node: "w1",
            legacy: false,
            gaps: [{ gap_id: "creature:worker", saved_pwd: "/gone/a" }],
          },
          {
            sid: "b",
            on_node: "w2",
            legacy: false,
            gaps: [{ gap_id: "creature:worker", saved_pwd: "/gone/b" }],
          },
        ],
        gaps: [],
      })
      .mockResolvedValueOnce({ ready: true, members: [], gaps: [] })
    const chooseWorkspace = vi
      .fn()
      .mockResolvedValueOnce({ action: "choose", path: "/new/a" })
      .mockResolvedValueOnce({ action: "choose", path: "/new/b" })

    const result = await prepareWorkspaceResume("cluster", { chooseWorkspace })

    expect(result.memberWorkspaceOverrides).toEqual({
      a: { "creature:worker": "/new/a" },
      b: { "creature:worker": "/new/b" },
    })
    expect(result.members).toEqual([
      { sid: "a", on_node: "w1" },
      { sid: "b", on_node: "w2" },
    ])
  })

  it("uses the installed chooser supplied by the shared dialog", async () => {
    preflightResume.mockResolvedValue({
      ready: false,
      gaps: [{ gap_id: "path:a", saved_pwd: "/gone" }],
    })
    const { installWorkspaceResumeResolver } = await import("./workdirPrompt")
    const chooser = vi.fn().mockResolvedValue({ action: "cancel" })
    const uninstall = installWorkspaceResumeResolver(chooser)

    await expect(prepareWorkspaceResume("saved")).resolves.toEqual({
      action: "cancel",
      workspaceOverrides: {},
    })
    expect(chooser).toHaveBeenCalledWith({
      sessionName: "saved",
      gap: { gap_id: "path:a", saved_pwd: "/gone" },
      label: "/gone",
    })
    uninstall()
  })

  it("returns history without validation or resume-side data", async () => {
    preflightResume.mockResolvedValue({
      ready: false,
      gaps: [{ gap_id: "path:a", saved_pwd: "/gone" }],
    })
    const chooseWorkspace = vi.fn().mockResolvedValue({ action: "history" })

    await expect(prepareWorkspaceResume("saved", { chooseWorkspace })).resolves.toEqual({
      action: "history",
      workspaceOverrides: {},
    })
    expect(preflightResume).toHaveBeenCalledTimes(1)
  })

  it("returns cancel without validation or resume-side data", async () => {
    preflightResume.mockResolvedValue({
      ready: false,
      gaps: [{ gap_id: "path:a", saved_pwd: "/gone" }],
    })
    const chooseWorkspace = vi.fn().mockResolvedValue({ action: "cancel" })

    await expect(prepareWorkspaceResume("saved", { chooseWorkspace })).resolves.toEqual({
      action: "cancel",
      workspaceOverrides: {},
    })
    expect(preflightResume).toHaveBeenCalledTimes(1)
  })
})
