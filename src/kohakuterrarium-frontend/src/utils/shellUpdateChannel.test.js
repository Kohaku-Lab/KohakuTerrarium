import { beforeEach, describe, expect, it, vi } from "vitest"

const { getSettings } = vi.hoisted(() => ({ getSettings: vi.fn() }))
vi.mock("@/utils/appUpdateApi", () => ({ appUpdateAPI: { getSettings } }))

import { followRuntimeChannel, shellChannelFor } from "./shellUpdateChannel"

function shell(current) {
  return { settings: { value: { updateChannel: current } }, patch: vi.fn(async () => true) }
}

beforeEach(() => {
  getSettings.mockReset()
})

describe("shellChannelFor", () => {
  it("follows nightly and treats every other runtime channel as stable", () => {
    expect(shellChannelFor("nightly")).toBe("nightly")
    expect(shellChannelFor("stable")).toBe("stable")
    expect(shellChannelFor("beta")).toBe("stable")
    expect(shellChannelFor(undefined)).toBe("stable")
  })
})

describe("followRuntimeChannel", () => {
  it("switches the shell to the runtime's channel when they differ", async () => {
    getSettings.mockResolvedValue({ channel: "nightly" })
    const handle = shell("stable")

    await followRuntimeChannel(handle)

    expect(handle.patch).toHaveBeenCalledWith({ updateChannel: "nightly" })
  })

  it("leaves the shell alone when it already matches or the runtime channel is unknown", async () => {
    getSettings.mockResolvedValue({ channel: "stable" })
    const same = shell("stable")
    await followRuntimeChannel(same)
    expect(same.patch).not.toHaveBeenCalled()

    getSettings.mockImplementation(async () => {
      throw new Error("409")
    })
    const unknown = shell("nightly")
    await followRuntimeChannel(unknown)
    expect(unknown.patch).not.toHaveBeenCalled()
  })
})
