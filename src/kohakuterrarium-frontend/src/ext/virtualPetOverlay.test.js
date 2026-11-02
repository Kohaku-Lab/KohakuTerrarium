import { readFileSync } from "node:fs"

import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { createMockHost } from "@/ext/sdk/mock"
import register, { PetCompanion } from "../../../../examples/packages/virtual_pet/ui/index.js"
import { cellFor, PetPlayer } from "../../../../examples/packages/virtual_pet/ui/player.js"
import { PetSprite } from "../../../../examples/packages/virtual_pet/ui/sprite.js"
import { PetSettings } from "../../../../examples/packages/virtual_pet/ui/settings.js"

const companionStyles = readFileSync("../../examples/packages/virtual_pet/ui/style.css", "utf8")
const atlas = {
  key: "a",
  displayName: "Test pet",
  width: 1536,
  height: 2288,
  cellWidth: 192,
  cellHeight: 208,
  frames: { idle: 7, running: 8 },
}
const image =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP1sAAAAASUVORK5CYII="
const work = (started = 0, status = "idle", sessionId = "g") => ({
  hostId: null,
  sessionId,
  label: "Session",
  started,
  status,
  busy: status === "running" || status === "waiting",
})
const state = (pet = atlas) => ({ pets: pet ? [pet] : [], selected: pet?.key || null })
let wrappers, frames, stylesheet, resizeObservers, decode
function render(component, options) {
  const w = mount(component, options)
  wrappers.push(w)
  return w
}
async function frame(now) {
  const callbacks = [...frames.values()]
  frames.clear()
  callbacks.forEach((fn) => fn(now))
  await flushPromises()
}
beforeEach(() => {
  stylesheet = document.createElement("style")
  stylesheet.textContent = companionStyles
  document.head.append(stylesheet)
  wrappers = []
  frames = new Map()
  resizeObservers = new Map()
  let id = 0
  vi.spyOn(performance, "now").mockReturnValue(0)
  decode = vi.fn().mockResolvedValue(undefined)
  vi.stubGlobal(
    "Image",
    class {
      decode() {
        return decode(this.src)
      }
    },
  )
  vi.stubGlobal("requestAnimationFrame", (fn) => {
    frames.set(++id, fn)
    return id
  })
  vi.stubGlobal("cancelAnimationFrame", (id) => frames.delete(id))
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback) {
        this.callback = callback
      }
      observe(element) {
        this.element = element
        resizeObservers.set(element, this.callback)
      }
      disconnect() {
        resizeObservers.delete(this.element)
      }
    },
  )
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }))
})
afterEach(() => {
  wrappers.forEach((w) => w.unmount())
  stylesheet.remove()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it("uses six-frame holds and a bounded session-start cue without replaying baselines", () => {
  for (const [action, holds] of [
    ["idle", [1680, 660, 660, 840, 840, 1920]],
    ["running", [120, 120, 120, 120, 120, 220]],
  ]) {
    let t = 0
    holds.forEach((hold, column) => {
      expect(cellFor(atlas, action, t)).toEqual({ row: action === "idle" ? 0 : 7, column })
      expect(cellFor(atlas, action, t + hold - 0.01).column).toBe(column)
      t += hold
    })
    expect(cellFor(atlas, action, t).column).toBe(0)
  }
  const p = new PetPlayer(atlas)
  p.setWork(work(3, "running"), 0)
  expect(p.tick(0).action).toBe("idle")
  p.setWork(work(4, "running"), 100)
  expect(p.tick(220)).toEqual({ row: 7, column: 1, action: "running" })
  p.setWork(work(4, "running"), 2000)
  expect(p.tick(2559.99)).toEqual({ row: 7, column: 5, action: "running" })
  expect(p.tick(2560)).toEqual({ row: 0, column: 0, action: "idle" })
  p.setWork(work(4, "running"), 2700)
  expect(p.tick(2700).action).toBe("idle")
  p.setWork(work(12, "running", "another"), 2800)
  expect(p.tick(2800).action).toBe("idle")
  p.setWork(work(13, "running", "another"), 3000)
  p.setWork(work(13, "waiting", "another"), 3100)
  expect(p.tick(3100).action).toBe("idle")
  p.setWork(work(13, "running", "another"), 3240)
  expect(p.tick(3240).column).toBe(2)
  expect(p.tick(3240, true).action).toBe("idle")
  p.setWork(null, 3300)
  expect(p.tick(3300).action).toBe("idle")
  const short = new PetPlayer({ ...atlas, frames: { idle: 1, running: 3 } })
  short.setWork(work(), 0)
  short.setWork(work(1, "running"), 100)
  expect(short.tick(1179).action).toBe("running")
  expect(short.tick(1180).action).toBe("idle")
})

it("renders scheduled frames and consumes hidden deadlines, cleans up and respects still mode", async () => {
  const w = render(PetSprite, { props: { atlas, source: image, work: work() } })
  await w.setProps({ work: work(1, "running") })
  await frame(120)
  expect(w.find(".vp-sprite").element.style.backgroundPosition).toBe("-192px -1456px")
  vi.mocked(performance.now).mockReturnValue(3000)
  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true)
  document.dispatchEvent(new Event("visibilitychange"))
  await flushPromises()
  expect(frames.size).toBe(0)
  hidden.mockReturnValue(false)
  document.dispatchEvent(new Event("visibilitychange"))
  await flushPromises()
  expect(w.find(".vp-sprite").attributes("data-action")).toBe("idle")
  await w.setProps({ paused: true, work: work(2, "running") })
  expect(frames.size).toBe(0)
  expect(w.find(".vp-sprite").attributes("data-action")).toBe("idle")
  await w.setProps({ paused: false })
  expect(frames.size).toBe(1)
  await w.setProps({ source: image + "NEW" })
  expect(w.find(".vp-sprite").attributes("data-action")).toBe("idle")
  const stage = w.find(".vp-stage").element
  resizeObservers.get(stage)([{ contentRect: { width: 224, height: 80 } }])
  await flushPromises()
  expect(w.find(".vp-sprite").element.style.height).toBe("208px")
  await w.setProps({ fit: true })
  expect(w.find(".vp-sprite").element.style.height).toBe("56px")
  resizeObservers.get(stage)([{ contentRect: { width: 224, height: 24 } }])
  await flushPromises()
  expect(w.find(".vp-sprite").element.style.height).toBe("0px")
  resizeObservers.get(stage)([{ contentRect: { width: 112, height: 280 } }])
  await flushPromises()
  expect(w.find(".vp-sprite").element.style.width).toBe("96px")
  await w.setProps({ fit: false })
  expect(w.find(".vp-stage").element.style.minHeight).toBe("128px")
  w.unmount()
  expect(frames.size).toBe(0)
  expect(resizeObservers.size).toBe(0)
})

it("registers an overlay, follows session facts and keeps work bubble after the cue", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] })
  const host = createMockHost({
    owner: { state: state(), actions: { sprite: () => image } },
    work: work(),
  })
  await host.register(register)
  expect(host.registered.panel.size).toBe(0)
  const w = render(host.registered.overlay.get("pet").component)
  await flushPromises()
  host.state.work = work(1, "running")
  await flushPromises()
  expect(w.find(".vp-sprite").attributes("data-action")).toBe("running")
  await frame(2460)
  expect(w.find(".vp-sprite").attributes("data-action")).toBe("idle")
  expect(w.find(".vp-bubble").text()).toBe("Processing")
  host.state.work = work(9, "running", "other")
  await flushPromises()
  expect(w.find(".vp-sprite").attributes("data-action")).toBe("idle")
  host.state.work = null
  await flushPromises()
  expect(w.find(".vp-bubble").exists()).toBe(false)
  expect(host.ownerCalls).toHaveLength(1)
  const region = w.find("[role=status]").element
  document.body.append(w.element)
  const hiddenStyle = getComputedStyle(region)
  expect(hiddenStyle.position).toBe("absolute")
  expect(hiddenStyle.top).toBe("0px")
  expect(hiddenStyle.left).toBe("0px")
  expect(getComputedStyle(w.find(".vp-controls").element).position).not.toBe("absolute")
  expect(w.find(".vp-drag").classes()).not.toContain("kt-overlay-hit")
  expect(w.find(".vp-sprite").classes()).toContain("kt-overlay-hit")
  host.state.work = work(10, "running")
  await flushPromises()
  host.state.work = work(10, "ready")
  await flushPromises()
  expect(w.find(".vp-bubble").text()).toBe("Response ready")
  vi.advanceTimersByTime(2000)
  host.state.locale = "zh-CN"
  await flushPromises()
  expect(w.find(".vp-bubble").text()).toBe("回复就绪")
  expect(w.find("[role=status]").element).toBe(region)
  vi.advanceTimersByTime(1000)
  await flushPromises()
  expect(w.find(".vp-bubble").exists()).toBe(false)
  host.state.work = work(10, "ready")
  await flushPromises()
  expect(w.find(".vp-bubble").exists()).toBe(false)
  host.state.locale = "en"
  await flushPromises()
  await w.find("[aria-label='Hide pet']").trigger("click")
  expect(host.overlayVisible[host.overlayId]).toBe(false)
  await w.find("[aria-label='Pet settings']").trigger("click")
  expect(host.openedTabs[0].section).toBe("ext:test-pkg/pets")
})

it("arbitrates direct gestures without converting drag to click or replaying appearances", async () => {
  const rich = {
    ...atlas,
    key: "interactive",
    interactions: { waving: 4, jumping: 5, "running-left": 8, "running-right": 8 },
  }
  let now = 0
  vi.mocked(performance.now).mockImplementation(() => now)
  const pointer = (target, type, x = 50, y = 50, id = 1, pointerType = "mouse") => {
    const event = new MouseEvent(type, {
      bubbles: true,
      cancelable: true,
      button: 0,
      buttons: type === "pointerdown" || type === "pointermove" ? 1 : 0,
      clientX: x,
      clientY: y,
    })
    Object.defineProperties(event, {
      pointerId: { value: id },
      pointerType: { value: pointerType },
      isPrimary: { value: id === 1 },
    })
    target.dispatchEvent(event)
  }
  const host = createMockHost({
    host: { id: "interaction-host" },
    owner: { state: state(rich), actions: { sprite: () => image } },
  })
  const w = render(PetCompanion)
  await flushPromises()
  const action = () => w.find(".vp-sprite").attributes("data-action")
  const sprite = w.findComponent(PetSprite)
  expect(action()).toBe("waving")
  expect(w.find(".vp-sprite").attributes("role")).toBe("button")
  now = 2100
  await frame(now)
  expect(action()).toBe("idle")
  const hit = w.find(".vp-sprite").element
  pointer(hit, "pointerenter")
  await flushPromises()
  expect(action()).toBe("jumping")
  now = 2200
  pointer(hit, "pointerenter")
  await flushPromises()
  now = 4620
  await frame(now)
  expect(action()).toBe("idle")
  now = 4700
  pointer(hit, "pointerdown")
  await flushPromises()
  expect(action()).toBe("idle")
  pointer(window, "pointermove", 52, 52)
  await flushPromises()
  expect(sprite.emitted("drag-start")).toBeUndefined()
  now = 4800
  pointer(window, "pointermove", 65, 50)
  await flushPromises()
  expect(sprite.emitted("drag-start")).toHaveLength(1)
  expect(action()).toBe("running-right")
  now = 4900
  pointer(window, "pointermove", 63, 50)
  await flushPromises()
  expect(action()).toBe("running-right")
  pointer(window, "pointermove", 54, 50)
  await flushPromises()
  expect(action()).toBe("running-left")
  now = 8080
  await frame(now)
  expect(action()).toBe("idle")
  now = 8200
  pointer(window, "pointermove", 45, 50)
  await flushPromises()
  expect(action()).toBe("idle")
  now = 8300
  pointer(window, "pointermove", 51, 50)
  await flushPromises()
  expect(action()).toBe("running-right")
  expect(w.findComponent(PetSprite).vm.cell).toMatchObject({ row: 1, column: 0 })
  pointer(window, "pointerup", 51, 50)
  await flushPromises()
  expect(action()).toBe("idle")
  now = 8400
  pointer(hit, "pointerdown")
  pointer(window, "pointerup")
  await flushPromises()
  expect(action()).toBe("waving")
  now = 8500
  pointer(hit, "pointerdown")
  pointer(window, "pointerup")
  await flushPromises()
  now = 10500
  await frame(now)
  expect(action()).toBe("idle")
  pointer(hit, "pointerenter", 50, 50, 1, "touch")
  await flushPromises()
  expect(action()).toBe("idle")
  await w.find(".vp-sprite").trigger("keydown", { key: "Enter" })
  expect(action()).toBe("waving")
  vi.spyOn(document, "hidden", "get").mockReturnValue(true)
  document.dispatchEvent(new Event("visibilitychange"))
  await flushPromises()
  expect(action()).toBe("idle")
  vi.spyOn(document, "hidden", "get").mockReturnValue(false)
  document.dispatchEvent(new Event("visibilitychange"))
  await flushPromises()
  expect(action()).toBe("idle")
  await w.find('[aria-label="Pet settings"]').trigger("click")
  expect(action()).toBe("idle")
  w.unmount()
  const reopened = render(PetCompanion)
  await flushPromises()
  expect(reopened.find(".vp-sprite").attributes("data-action")).toBe("idle")
  host.state.work = work(7, "running", "new-session")
  await flushPromises()
  expect(reopened.find(".vp-sprite").attributes("data-action")).toBe("idle")
  host.emit("state", state({ ...rich, key: "different-pet" }))
  await flushPromises()
  expect(reopened.find(".vp-sprite").attributes("data-action")).toBe("waving")
  const reSprite = reopened.findComponent(PetSprite)
  pointer(reopened.find(".vp-sprite").element, "pointerdown")
  reopened.unmount()
  pointer(window, "pointermove", 100, 50)
  pointer(window, "pointerup", 100, 50)
  expect(reSprite.emitted("drag-start")).toBeUndefined()
})

it("drops old artwork after backend/selection changes and retries a failed image", async () => {
  let finish,
    fail = false
  const host = createMockHost({
    owner: {
      state: state(),
      actions: {
        sprite: ({ key }) => {
          if (key === "a")
            return new Promise((resolve) => {
              finish = resolve
            })
          if (fail) throw Error("offline")
          return image + key
        },
      },
    },
  })
  const w = render(PetCompanion)
  host.emit("state", state({ ...atlas, key: "b" }))
  await flushPromises()
  finish(image + "OLD")
  await flushPromises()
  expect(w.find(".vp-sprite").element.style.backgroundImage).toContain(image + "b")
  fail = true
  host.state.host = { id: "new", url: "https://another" }
  await flushPromises()
  expect(w.find("[role=alert]").text()).toContain("Couldn't load")
  fail = false
  await w.find("[role=alert] button").trigger("click")
  await flushPromises()
  expect(w.find(".vp-sprite").exists()).toBe(true)
  expect(w.find(".vp-sprite").attributes("aria-label")).toBe("Test pet")
})

it("waits for browser decoding, retries failure without consuming greeting, and drops stale decode completions", async () => {
  let rejectDecode
  decode.mockImplementationOnce(
    () =>
      new Promise((_, reject) => {
        rejectDecode = reject
      }),
  )
  const rich = { ...atlas, key: "decode-first", interactions: { waving: 4 } }
  const host = createMockHost({
    host: { id: "decode-host" },
    owner: { state: state(rich), actions: { sprite: () => image } },
  })
  const w = render(PetCompanion)
  await flushPromises()
  expect(w.find(".vp-sprite").exists()).toBe(false)
  expect(decode).toHaveBeenCalledWith(image)
  rejectDecode(Error("cannot decode"))
  await flushPromises()
  expect(w.find("[role=alert]").text()).toContain("Couldn't load")
  await w.find("[role=alert] button").trigger("click")
  await flushPromises()
  expect(w.find(".vp-sprite").attributes("data-action")).toBe("waving")
  let finishDecode
  decode.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finishDecode = resolve
      }),
  )
  host.emit("state", state({ ...rich, key: "late-decode" }))
  await flushPromises()
  expect(w.find(".vp-sprite").exists()).toBe(false)
  host.state.host = { id: "decode-other", url: "https://decode-other" }
  await flushPromises()
  expect(w.find(".vp-sprite").attributes("data-action")).toBe("waving")
  finishDecode()
  await flushPromises()
  w.unmount()
  host.state.host = { id: "decode-host" }
  const returned = render(PetCompanion)
  await flushPromises()
  expect(returned.find(".vp-sprite").attributes("data-action")).toBe("waving")
})

it("invalidates confirmations and dispatched mutations when the backend changes", async () => {
  const other = { ...atlas, key: "other", displayName: "Other pet" }
  const host = createMockHost({
    owner: {
      state: { pets: [atlas, other], selected: atlas.key },
      actions: { sprite: () => image, thumbnail: () => image, select: () => {}, remove: () => {} },
    },
  })
  const w = render(PetSettings)
  await flushPromises()
  let confirm
  host.state.confirmAnswer = new Promise((resolve) => {
    confirm = resolve
  })
  await w.find(".vp-remove").trigger("click")
  await flushPromises()
  expect(w.attributes("aria-busy")).toBe("true")
  host.state.host = { id: "confirmation-new", url: "https://confirmation-new" }
  await flushPromises()
  confirm(true)
  await flushPromises()
  expect(host.ownerCalls.some((call) => call.action === "remove")).toBe(false)
  expect(w.attributes("aria-busy")).toBe("false")
  const call = host.owner.call.bind(host.owner)
  const pending = []
  vi.spyOn(host.owner, "call").mockImplementation((action, args) =>
    action === "select"
      ? new Promise((resolve, reject) => pending.push({ resolve, reject }))
      : call(action, args),
  )
  const refresh = vi.spyOn(host.owner, "refresh")
  await w.find('[aria-label="Choose Other pet"]').trigger("click")
  await flushPromises()
  expect(pending).toHaveLength(1)
  host.state.host = { id: "mutation-new", url: "https://mutation-new" }
  await flushPromises()
  expect(w.attributes("aria-busy")).toBe("false")
  await w.find('[aria-label="Choose Other pet"]').trigger("click")
  await flushPromises()
  expect(pending).toHaveLength(2)
  pending[0].reject(Error("old backend failed"))
  await flushPromises()
  expect(w.find("[role=alert]").exists()).toBe(false)
  expect(w.attributes("aria-busy")).toBe("true")
  expect(refresh).not.toHaveBeenCalled()
  pending[1].resolve()
  await flushPromises()
  expect(w.attributes("aria-busy")).toBe("false")
  expect(refresh).toHaveBeenCalledTimes(1)
})

it("manages the same library in settings without a creature and discards stale imports", async () => {
  const host = createMockHost({
    owner: {
      state: state(null),
      actions: {
        import: () => host.emit("state", state()),
        sprite: () => image,
        thumbnail: () => image,
        remove: () => host.emit("state", state(null)),
        select: () => {},
      },
    },
  })
  const w = render(PetSettings)
  const file = { size: 3, arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer }
  Object.defineProperty(w.find("input[type=file]").element, "files", {
    value: [file],
    configurable: true,
  })
  await w.find("input[type=file]").trigger("change")
  await flushPromises()
  expect(host.ownerCalls[0]).toEqual({ action: "import", args: { archive: "AQID" } })
  expect(w.findComponent(PetSprite).exists()).toBe(true)
  await w.find(".vp-pet-card img").trigger("error")
  expect(w.find(".vp-pet-card img").exists()).toBe(false)
  expect(w.find(".vp-pet-mark").exists()).toBe(true)
  await w.setProps({ active: false })
  decode.mockRejectedValueOnce(Error("bad image"))
  await w.setProps({ active: true })
  await flushPromises()
  expect(w.find(".vp-preview [role=alert]").text()).toContain("Couldn't load")
  expect(w.findComponent(PetSprite).exists()).toBe(false)
  await w.find(".vp-preview [role=alert] button").trigger("click")
  await flushPromises()
  expect(w.findComponent(PetSprite).exists()).toBe(true)
  expect(w.find("h2").exists()).toBe(false)
  expect(w.text()).not.toContain("creature's panels")
  host.state.locale = "zh-CN"
  await flushPromises()
  expect(w.text()).toContain("当前伙伴")
  expect(w.find(".vp-remove").text()).toBe("移除此宠物")
  expect(w.find("input[type=file]").attributes("hidden")).toBeDefined()
  await w
    .findAll("button")
    .find((button) => button.text() === "打开浮层设置")
    .trigger("click")
  expect(host.openedTabs.at(-1).section).toBe("overlays")
  Object.defineProperty(w.find("input[type=file]").element, "files", {
    value: [{ size: 9 * 1024 * 1024 }],
    configurable: true,
  })
  await w.find("input[type=file]").trigger("change")
  await flushPromises()
  expect(w.find("[role=alert]").text()).toContain("8 MiB")
  expect(w.findComponent(PetSprite).exists()).toBe(true)
  expect(host.ownerCalls.filter((call) => call.action === "import")).toHaveLength(1)
  await w.find("[role=alert] button").trigger("click")
  await flushPromises()
  expect(w.find("[role=alert]").exists()).toBe(false)
  host.state.confirmAnswer = false
  await w.find(".vp-remove").trigger("click")
  await flushPromises()
  expect(w.findComponent(PetSprite).exists()).toBe(true)
  expect(host.ownerCalls.filter((call) => call.action === "remove")).toHaveLength(0)
  host.state.confirmAnswer = true
  await w.find(".vp-remove").trigger("click")
  await flushPromises()
  expect(w.findComponent(PetSprite).exists()).toBe(false)
  let release
  const delayed = {
    size: 1,
    arrayBuffer: () =>
      new Promise((resolve) => {
        release = resolve
      }),
  }
  Object.defineProperty(w.find("input[type=file]").element, "files", {
    value: [delayed],
    configurable: true,
  })
  await w.find("input[type=file]").trigger("change")
  host.state.host = { id: "other", url: "https://other" }
  await flushPromises()
  release(new Uint8Array([5]).buffer)
  await flushPromises()
  expect(host.ownerCalls.filter((call) => call.action === "import")).toHaveLength(1)
})
