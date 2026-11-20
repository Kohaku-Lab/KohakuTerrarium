import { afterEach, expect, it, vi } from "vitest"
import { connectSettings } from "../../../../examples/packages/live2d_pet/ui/stage-settings.js"
import { normalizePreferences } from "../../../../examples/packages/live2d_pet/ui/preferences.js"

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  history.replaceState(null, "", "/")
  document.body.replaceChildren()
  localStorage.clear()
})

it("hydrates the child, updates quick settings, accepts only its current parent, and releases its listener", () => {
  const prefs = normalizePreferences({
    version: 1,
    debug: false,
    models: { dragon: { expressions: ["变"] } },
  })
  history.replaceState(
    null,
    "",
    `/stage.html?channel=bound&preferences=${encodeURIComponent(JSON.stringify(prefs))}`,
  )
  vi.spyOn(document, "referrer", "get").mockReturnValue("http://localhost/")
  document.body.innerHTML = '<section id="pet-menu"><div class="pet-menu-actions"></div></section>'
  const postMessage = vi.fn()
  const host = { postMessage }
  vi.stubGlobal("parent", host)
  const changed = vi.fn()
  const settings = connectSettings(changed)
  try {
    settings.render()
    expect(settings.value.models.dragon.expressions).toEqual(["变"])
    expect(document.body.classList.contains("debug")).toBe(false)
    const size = document.querySelector('input[aria-label="宠物大小"]')
    const output = document.querySelector("output")
    const callsBeforeDrag = changed.mock.calls.length
    for (const value of ["120", "140", "160"]) {
      size.value = value
      size.dispatchEvent(new Event("input"))
      expect(output.textContent).toBe(`${value}%`)
      expect(settings.value.models.dragon.scale).toBe(1)
      expect(changed).toHaveBeenCalledTimes(callsBeforeDrag)
      expect(postMessage).not.toHaveBeenCalled()
    }
    const send = (channel, origin = "http://localhost", source = host) =>
      window.dispatchEvent(
        new MessageEvent("message", {
          source,
          origin,
          data: { type: "kt-live2d-settings", channel, preferences: { ...prefs, debug: true } },
        }),
      )
    send("stale")
    send("bound", "https://foreign.invalid")
    send("bound", "http://localhost", null)
    expect(settings.value.debug).toBe(false)
    send("bound")
    expect(document.body.classList.contains("debug")).toBe(true)
    expect(size.value).toBe("160")
    size.dispatchEvent(new Event("change"))
    expect(settings.value.models.dragon.scale).toBe(1.6)
    expect(postMessage).toHaveBeenCalledTimes(1)
    expect(postMessage.mock.lastCall[0].patch).toEqual({ models: { dragon: { scale: 1.6 } } })
    size.dispatchEvent(new Event("blur"))
    expect(postMessage).toHaveBeenCalledTimes(1)
    size.value = "200"
    size.dispatchEvent(new Event("input"))
    size.dispatchEvent(new Event("pointercancel"))
    expect(size.value).toBe("160")
    expect(output.textContent).toBe("160%")
    expect(postMessage).toHaveBeenCalledTimes(1)
    size.value = "165"
    size.dispatchEvent(new Event("input"))
    size.dispatchEvent(new Event("change"))
    expect(settings.value.models.dragon.scale).toBe(1.65)
    size.value = "180"
    size.dispatchEvent(new Event("input"))
    size.dispatchEvent(new Event("blur"))
    expect(settings.value.models.dragon.scale).toBe(1.8)
    expect(postMessage).toHaveBeenCalledTimes(3)
    expect(changed.mock.lastCall[0].models.dragon.expressions).toEqual(["变"])
    const mouth = document.querySelector('input[aria-label="说话口型"]')
    expect(mouth.checked).toBe(true)
    mouth.checked = false
    mouth.dispatchEvent(new Event("change"))
    expect(settings.value.talkingMouth).toBe(false)
    expect(postMessage.mock.lastCall[0].patch).toEqual({ talkingMouth: false })
    settings.dispose()
    const calls = changed.mock.calls.length
    send("bound")
    expect(changed).toHaveBeenCalledTimes(calls)
    expect(document.querySelector(".pet-quick-settings")).toBeNull()
  } finally {
    settings.dispose()
  }
})
