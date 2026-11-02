import { afterEach, describe, expect, it, vi } from "vitest"

import { ACTION_EVENT, showActionToast } from "@/utils/actionNotification"

function shell(result) {
  const show = vi.fn().mockResolvedValue(result)
  window.pywebview = { api: { show_action_notification: show } }
  return show
}

const press = (token, id) =>
  window.dispatchEvent(new CustomEvent(ACTION_EVENT, { detail: { token, id } }))

afterEach(() => {
  delete window.pywebview
})

describe("showActionToast", () => {
  it("calls back once with the pressed button, or null for the notification itself", async () => {
    const show = shell(true)
    const chosen = vi.fn()

    expect(
      await showActionToast({ title: "t", body: "b", actions: [{ id: "a", label: "A" }] }, chosen),
    ).toBe(true)
    const token = show.mock.calls[0][3]
    press(token, "a")
    press(token, "a")
    expect(chosen.mock.calls).toEqual([["a"]])

    await showActionToast({ title: "t", body: "b", actions: [{ id: "a", label: "A" }] }, chosen)
    press(show.mock.calls[1][3], null)
    expect(chosen.mock.calls.at(-1)).toEqual([null])
  })

  it("forgets the callback when the shell shows nothing, and ignores unknown tokens", async () => {
    const show = shell(false)
    const chosen = vi.fn()

    expect(await showActionToast({ title: "t", body: "b", actions: [] }, chosen)).toBe(false)
    press(show.mock.calls[0][3], "a")
    press("unknown", "a")

    expect(chosen).not.toHaveBeenCalled()
  })

  it("keeps only the latest pending notifications", async () => {
    const show = shell(true)
    const callbacks = Array.from({ length: 25 }, () => vi.fn())
    for (const callback of callbacks) {
      await showActionToast({ title: "t", body: "b", actions: [] }, callback)
    }

    press(show.mock.calls[0][3], "a")
    press(show.mock.calls[24][3], "a")

    expect(callbacks[0]).not.toHaveBeenCalled()
    expect(callbacks[24]).toHaveBeenCalledWith("a")
  })

  it("does nothing without a shell", async () => {
    expect(await showActionToast({ title: "t", body: "b", actions: [] }, vi.fn())).toBe(false)
  })
})
