// Focused guard for the ACTUAL production failure mode of markdown media: a
// Host read that REJECTS. The shared inline ``<img>`` resolver used to silently
// clear the ``src`` (a blank node) where the component leaves
// (``MediaImage`` / ``VideoFilePreview``) show the production
// ``.kt-media-status.is-error`` leaf. These tests pin the fixed contract:
//
//   * a failed artifact renders the SAME visible, localized status leaf (real
//     ``chat.media.*`` dictionary text, never the raw Host rejection / a token),
//   * the raw source + alt survive so a retry/removal still recognizes it,
//   * a streaming re-render of the unchanged source re-plays the status WITHOUT
//     a fresh Host read per frame (the old bug refetched every frame),
//   * only an EXPLICIT ``retry`` / a fence flip issues a fresh read; the surface
//     adds no fake button because it owns no per-image handler,
//   * the browser resolver default (no Host transport) is unchanged.
import { flushPromises, mount } from "@vue/test-utils"
import { nextTick, ref } from "vue"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import mediaDictionary from "@/utils/i18n/locales/media.js"

import MarkdownRenderer from "./MarkdownRenderer.vue"
import {
  MEDIA_RESOLVER_KEY,
  createBrowserMediaResolver,
  createMarkdownMediaResolver,
} from "./mediaResolver.js"

// The REAL production dictionary entry the shared error status must render.
const UNAVAILABLE = mediaDictionary.en["chat.media.unavailable"]
const ARTIFACT = "/api/sessions/s1/artifacts/gen/pic.png"

// A Host resolver shaped exactly like the VS Code bridge: it carries the real
// dictionary provider on ``translate`` and answers ``resolveImage`` async.
function hostResolver(overrides = {}) {
  return {
    kind: "host",
    canOpen: true,
    canSave: true,
    translate: (key) => mediaDictionary.en[key] ?? key,
    resolveImage: vi.fn(),
    resolveMedia: vi.fn(),
    release: vi.fn(),
    open: vi.fn(),
    save: vi.fn(),
    ...overrides,
  }
}

async function flushMicrotasks() {
  await nextTick()
  for (let i = 0; i < 10; i++) await Promise.resolve()
  await nextTick()
}

describe("createMarkdownMediaResolver failure", () => {
  it("renders the shared visible localized error leaf once and keeps the source for explicit retry", async () => {
    const root = document.createElement("div")
    root.innerHTML = `<p><img src="${ARTIFACT}" alt="diagram"></p>`
    const resolver = hostResolver({
      resolveImage: vi.fn().mockRejectedValue(new Error("prepare failed token=host-secret")),
    })
    const md = createMarkdownMediaResolver(resolver)

    md.resolve(root)
    await flushPromises()

    expect(resolver.resolveImage).toHaveBeenCalledTimes(1)
    const status = root.querySelector(".kt-media-status.is-error")
    expect(status).toBeTruthy()
    expect(status.getAttribute("role")).toBe("status")
    // Genuine localized dictionary text, never the raw rejection.
    expect(status.textContent).toBe(UNAVAILABLE)
    expect(status.textContent).not.toContain("host-secret")
    expect(root.innerHTML).not.toContain("host-secret")
    // Visible text (not an invisible title-only tooltip) and no fake control.
    expect(status.textContent.trim().length).toBeGreaterThan(0)
    expect(status.querySelector("button")).toBeNull()
    // Source preserved so a later retry/removal still recognizes it.
    expect(status.getAttribute("data-media-src")).toBe(ARTIFACT)

    // Streaming re-render: the same source reappears on a fresh node. The kept
    // op replays the status with NO new Host read (no per-frame retry loop).
    root.innerHTML = `<p>streamed text</p><p><img src="${ARTIFACT}" alt="diagram"></p>`
    md.resolve(root)
    await flushPromises()
    expect(resolver.resolveImage).toHaveBeenCalledTimes(1)
    expect(root.querySelector(".kt-media-status.is-error").textContent).toBe(UNAVAILABLE)

    // Only an EXPLICIT retry issues a fresh read.
    md.retry(ARTIFACT)
    await flushPromises()
    expect(resolver.resolveImage).toHaveBeenCalledTimes(2)

    md.dispose()
    expect(resolver.release).not.toHaveBeenCalled()
  })

  it("clears the error status and restores the image when a later generation resolves, releasing once", async () => {
    const root = document.createElement("div")
    root.innerHTML = `<img src="${ARTIFACT}" alt="pic">`
    const generation = ref(0)
    const resolveImage = vi
      .fn()
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce({ url: "vscode-webview://spool/ok", resourceId: "r1" })
    const resolver = hostResolver({ generation, resolveImage })
    const md = createMarkdownMediaResolver(resolver)

    md.resolve(root)
    await flushPromises()
    expect(root.querySelector(".kt-media-status.is-error")).toBeTruthy()

    generation.value += 1
    await nextTick()
    await flushPromises()

    expect(resolveImage).toHaveBeenCalledTimes(2)
    const img = root.querySelector("img")
    expect(img).toBeTruthy()
    expect(img.getAttribute("src")).toBe("vscode-webview://spool/ok")
    expect(root.querySelector(".kt-media-status.is-error")).toBeNull()

    md.dispose()
    expect(resolver.release).toHaveBeenCalledTimes(1)
    expect(resolver.release).toHaveBeenCalledWith("r1")
  })

  it("drops a failed source and its status once it leaves the render, without retrying", async () => {
    const root = document.createElement("div")
    root.innerHTML = `<img src="${ARTIFACT}" alt="pic">`
    const resolver = hostResolver({ resolveImage: vi.fn().mockRejectedValue(new Error("boom")) })
    const md = createMarkdownMediaResolver(resolver)

    md.resolve(root)
    await flushPromises()
    expect(root.querySelector(".kt-media-status.is-error")).toBeTruthy()

    root.innerHTML = `<p>no images</p>`
    md.resolve(root)
    await flushPromises()
    expect(root.querySelector(".kt-media-status.is-error")).toBeNull()
    expect(resolver.resolveImage).toHaveBeenCalledTimes(1)

    md.dispose()
    expect(resolver.release).not.toHaveBeenCalled()
  })

  it("uses the dictionary's translated text for the visible status", async () => {
    const root = document.createElement("div")
    root.innerHTML = `<img src="${ARTIFACT}">`
    const resolver = hostResolver({
      translate: () => mediaDictionary["zh-CN"]["chat.media.unavailable"],
      resolveImage: vi.fn().mockRejectedValue(new Error("boom")),
    })
    const md = createMarkdownMediaResolver(resolver)
    md.resolve(root)
    await flushPromises()
    expect(root.querySelector(".kt-media-status.is-error").textContent).toBe(
      mediaDictionary["zh-CN"]["chat.media.unavailable"],
    )
    md.dispose()
  })
})

describe("browser media resolver default", () => {
  it("keeps ordinary inline image behavior and never paints a status", () => {
    const root = document.createElement("div")
    root.innerHTML = `<img src="${ARTIFACT}" alt="pic">`
    const md = createMarkdownMediaResolver(createBrowserMediaResolver())
    md.resolve(root)
    expect(root.querySelector("img").getAttribute("src")).toBe(ARTIFACT)
    expect(root.querySelector(".kt-media-status")).toBeNull()
    md.dispose()
  })
})

describe("MarkdownRenderer failed markdown media", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it("shows one visible localized error status for a failed artifact across streaming frames", async () => {
    const resolver = hostResolver({
      resolveImage: vi.fn().mockRejectedValue(new Error("E_FAIL token=host-secret")),
    })
    const wrapper = mount(MarkdownRenderer, {
      props: { content: `![diagram](${ARTIFACT})` },
      global: { provide: { [MEDIA_RESOLVER_KEY]: resolver } },
    })
    await flushMicrotasks()

    const status = wrapper.find(".md-content .kt-media-status.is-error")
    expect(status.exists()).toBe(true)
    expect(status.attributes("role")).toBe("status")
    expect(status.text()).toBe(UNAVAILABLE)
    expect(wrapper.html()).not.toContain("host-secret")
    expect(status.find("button").exists()).toBe(false)

    await wrapper.setProps({ content: `![diagram](${ARTIFACT})\n\nmore streamed text` })
    await vi.advanceTimersByTimeAsync(200)
    await flushMicrotasks()

    expect(resolver.resolveImage).toHaveBeenCalledTimes(1)
    expect(wrapper.find(".md-content .kt-media-status.is-error").exists()).toBe(true)
    wrapper.unmount()
  })
})
