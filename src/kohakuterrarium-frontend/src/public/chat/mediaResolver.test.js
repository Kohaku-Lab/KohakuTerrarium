import { mount } from "@vue/test-utils"
import { nextTick, defineComponent, h, ref } from "vue"
import { describe, expect, it, vi } from "vitest"

import {
  MEDIA_RESOLVER_KEY,
  createBrowserMediaResolver,
  createMarkdownMediaResolver,
  useMediaResource,
} from "./mediaResolver.js"

const flush = () => nextTick()

function hostResolver(overrides = {}) {
  return {
    kind: "host",
    canOpen: true,
    canSave: true,
    resolveImage: vi.fn(),
    resolveMedia: vi.fn(),
    release: vi.fn(),
    open: vi.fn(),
    save: vi.fn(),
    ...overrides,
  }
}

function resourceProbe(resolver, value = "/api/sessions/s1/artifacts/clip.mp4") {
  let media = null
  const Comp = defineComponent({
    setup() {
      media = useMediaResource(() => value, { kind: "media" })
      return () => h("span", media.url.value || "empty")
    },
  })
  const wrapper = mount(Comp, { global: { provide: { [MEDIA_RESOLVER_KEY]: resolver } } })
  return {
    wrapper,
    get media() {
      return media
    },
  }
}

describe("createBrowserMediaResolver", () => {
  it("keeps direct same-origin URLs, maps file refs, and offers no lease", () => {
    const resolver = createBrowserMediaResolver()
    const artifact = "/api/sessions/s1/artifacts/gen/pic.png"
    expect(resolver.resolveImage(artifact)).toBe(artifact)
    expect(resolver.resolveImage("https://cdn.example/pic.png")).toBe("https://cdn.example/pic.png")
    expect(resolver.resolveImage("javascript:alert(1)")).toBe("")
    expect(resolver.resolveImage("file:///tmp/pic.png")).toBe(
      `/api/files/raw?path=${encodeURIComponent("/tmp/pic.png")}`,
    )
    expect(resolver.resolveMedia("file:///tmp/clip.mp4")).toBe(
      `/api/files/raw?path=${encodeURIComponent("/tmp/clip.mp4")}`,
    )
    expect(resolver.canOpen).toBe(false)
    expect(resolver.canSave).toBe(false)
  })
})

describe("useMediaResource host seam", () => {
  it("resolves async media and releases its resource lease on unmount", async () => {
    const resolver = hostResolver({
      resolveMedia: vi
        .fn()
        .mockResolvedValue({ url: "vscode-webview://spool/r1", resourceId: "r1" }),
    })
    const { wrapper, media } = resourceProbe(resolver)
    expect(media.state.value).toBe("loading")
    await flush()
    await flush()
    expect(wrapper.text()).toBe("vscode-webview://spool/r1")
    expect(media.state.value).toBe("ready")
    expect(media.canOpen).toBe(true)
    expect(media.canSave).toBe(true)
    wrapper.unmount()
    expect(resolver.release).toHaveBeenCalledWith("r1")
  })

  it("aborts an in-flight prepare on cancel instead of waiting for it to settle", async () => {
    let settle = null
    let cancelled = false
    const resolver = hostResolver({
      resolveMedia: vi.fn((_value, { signal }) => {
        signal.onCancel = () => {
          cancelled = true
        }
        return new Promise((resolve) => (settle = resolve))
      }),
    })
    const { wrapper, media } = resourceProbe(resolver)
    expect(media.state.value).toBe("loading")
    // Unmount while the Host read is still streaming: the resolver must be told
    // to cancel, and a late settle must not surface a URL or lease.
    wrapper.unmount()
    expect(cancelled).toBe(true)
    settle({ url: "vscode-webview://spool/late", resourceId: "late" })
    await flush()
    await flush()
    expect(media.url.value).toBe("")
    expect(resolver.release).not.toHaveBeenCalled()
  })

  it("re-resolves an unchanged reference when the resolver generation flips", async () => {
    const generation = ref(0)
    const resolver = hostResolver({
      generation,
      resolveMedia: vi
        .fn()
        .mockResolvedValue({ url: "vscode-webview://spool/r1", resourceId: "r1" }),
    })
    const { media } = resourceProbe(resolver)
    await flush()
    await flush()
    expect(resolver.resolveMedia).toHaveBeenCalledTimes(1)
    generation.value += 1
    await flush()
    await flush()
    expect(resolver.resolveMedia).toHaveBeenCalledTimes(2)
    expect(media.url.value).toBe("vscode-webview://spool/r1")
  })

  it("stops watching the generation after unmount instead of recreating a lease", async () => {
    const generation = ref(0)
    const resolver = hostResolver({
      generation,
      resolveMedia: vi
        .fn()
        .mockResolvedValue({ url: "vscode-webview://spool/r1", resourceId: "r1" }),
    })
    const { wrapper, media } = resourceProbe(resolver)
    await flush()
    await flush()
    expect(resolver.resolveMedia).toHaveBeenCalledTimes(1)
    wrapper.unmount()
    expect(resolver.release).toHaveBeenCalledWith("r1")
    // A fence change after unmount must not re-issue a Host read (the component
    // is gone; a resurrected prepare would orphan its spooled resource).
    generation.value += 1
    await flush()
    await flush()
    expect(resolver.resolveMedia).toHaveBeenCalledTimes(1)
    expect(media.url.value).toBe("")
  })
})

describe("createMarkdownMediaResolver", () => {
  const ARTIFACT = "/api/sessions/s1/artifacts/gen/pic.png"

  it("resolves an unchanged reference once across streaming re-renders and keeps its lease", async () => {
    const root = document.createElement("div")
    root.innerHTML = `<p><img src="${ARTIFACT}" alt="pic"></p>`
    const resolver = hostResolver({
      resolveImage: vi
        .fn()
        .mockResolvedValue({ url: "vscode-webview://spool/pic", resourceId: "r1" }),
    })
    const md = createMarkdownMediaResolver(resolver)
    md.resolve(root)
    await flush()
    await flush()
    expect(resolver.resolveImage).toHaveBeenCalledTimes(1)
    const first = root.querySelector("img")
    expect(first.getAttribute("src")).toBe("vscode-webview://spool/pic")
    // Streaming appends text: ``v-html`` recreates the subtree, so the swapped
    // node is gone but the raw reference is unchanged. No second Host prepare and
    // no release — the lease is reused and replayed onto the fresh node.
    root.innerHTML = `<p>more streamed text</p><p><img src="${ARTIFACT}" alt="pic"></p>`
    md.resolve(root)
    await flush()
    const next = root.querySelector("img")
    expect(next).not.toBe(first)
    expect(resolver.resolveImage).toHaveBeenCalledTimes(1)
    expect(next.getAttribute("src")).toBe("vscode-webview://spool/pic")
    expect(resolver.release).not.toHaveBeenCalled()
    md.dispose()
    expect(resolver.release).toHaveBeenCalledTimes(1)
    expect(resolver.release).toHaveBeenCalledWith("r1")
  })

  it("keeps a still-pending read alive across DOM recreation and lands it on the current node", async () => {
    const root = document.createElement("div")
    root.innerHTML = `<img src="${ARTIFACT}" alt="pic">`
    let settle = null
    const resolver = hostResolver({
      resolveImage: vi.fn(() => new Promise((resolve) => (settle = resolve))),
    })
    const md = createMarkdownMediaResolver(resolver)
    md.resolve(root)
    expect(root.querySelector("img").hasAttribute("src")).toBe(false)
    // A re-render replaces the node while the read is still in flight.
    root.innerHTML = `<p>x</p><img src="${ARTIFACT}" alt="pic">`
    md.resolve(root)
    const next = root.querySelector("img")
    expect(resolver.resolveImage).toHaveBeenCalledTimes(1)
    settle({ url: "vscode-webview://spool/pending", resourceId: "p1" })
    await flush()
    await flush()
    expect(next.getAttribute("src")).toBe("vscode-webview://spool/pending")
  })

  it("refcounts duplicate references to one source and releases exactly once", async () => {
    const root = document.createElement("div")
    root.innerHTML = `<img src="${ARTIFACT}"><img src="${ARTIFACT}">`
    const resolver = hostResolver({
      resolveImage: vi
        .fn()
        .mockResolvedValue({ url: "vscode-webview://spool/dup", resourceId: "dup1" }),
    })
    const md = createMarkdownMediaResolver(resolver)
    md.resolve(root)
    await flush()
    await flush()
    expect(resolver.resolveImage).toHaveBeenCalledTimes(1)
    const imgs = root.querySelectorAll("img")
    expect(imgs[0].getAttribute("src")).toBe("vscode-webview://spool/dup")
    expect(imgs[1].getAttribute("src")).toBe("vscode-webview://spool/dup")
    // Streaming drops ONE duplicate: the shared lease must survive.
    root.innerHTML = `<img src="${ARTIFACT}">`
    md.resolve(root)
    await flush()
    expect(resolver.release).not.toHaveBeenCalled()
    expect(root.querySelector("img").getAttribute("src")).toBe("vscode-webview://spool/dup")
    // The last reference leaves the render: released exactly once.
    root.innerHTML = `<p>done</p>`
    md.resolve(root)
    expect(resolver.release).toHaveBeenCalledTimes(1)
    expect(resolver.release).toHaveBeenCalledWith("dup1")
  })

  it("drops a reference removed from the render and releases it exactly once", async () => {
    const root = document.createElement("div")
    root.innerHTML = `<img src="${ARTIFACT}">`
    const resolver = hostResolver({
      resolveImage: vi
        .fn()
        .mockResolvedValue({ url: "vscode-webview://spool/gone", resourceId: "g1" }),
    })
    const md = createMarkdownMediaResolver(resolver)
    md.resolve(root)
    await flush()
    await flush()
    expect(resolver.release).not.toHaveBeenCalled()
    root.innerHTML = `<p>no images</p>`
    md.resolve(root)
    expect(resolver.release).toHaveBeenCalledTimes(1)
    expect(resolver.release).toHaveBeenCalledWith("g1")
  })

  it("invalidates an unchanged reference when the ready/selection generation flips", async () => {
    const root = document.createElement("div")
    root.innerHTML = `<img src="${ARTIFACT}">`
    const generation = ref(0)
    let seq = 0
    const resolver = hostResolver({
      generation,
      resolveImage: vi.fn(() =>
        Promise.resolve({ url: `vscode-webview://spool/${++seq}`, resourceId: `r${seq}` }),
      ),
    })
    const md = createMarkdownMediaResolver(resolver)
    md.resolve(root)
    await flush()
    await flush()
    expect(resolver.resolveImage).toHaveBeenCalledTimes(1)
    expect(root.querySelector("img").getAttribute("src")).toBe("vscode-webview://spool/1")
    generation.value += 1
    await flush()
    await flush()
    expect(resolver.resolveImage).toHaveBeenCalledTimes(2)
    expect(root.querySelector("img").getAttribute("src")).toBe("vscode-webview://spool/2")
    expect(resolver.release).toHaveBeenCalledWith("r1")
  })

  it("releases a lease that arrives after the pass was dropped exactly once", async () => {
    const root = document.createElement("div")
    root.innerHTML = `<img src="${ARTIFACT}" alt="pic">`
    let settle = null
    let cancelled = false
    const resolver = hostResolver({
      resolveImage: vi.fn((_value, { signal }) => {
        signal.onCancel = () => {
          cancelled = true
        }
        return new Promise((resolve) => (settle = resolve))
      }),
    })
    const md = createMarkdownMediaResolver(resolver)
    md.resolve(root)
    // The pass is superseded (or the component unmounts) while the read is pending.
    md.dispose()
    expect(cancelled).toBe(true)
    // The resolver resolves ANYWAY, carrying a lease handle that must be released
    // exactly once — never orphaned by the earlier drop bookkeeping.
    settle({ url: "vscode-webview://spool/late", resourceId: "late" })
    await flush()
    await flush()
    expect(resolver.release).toHaveBeenCalledTimes(1)
    expect(resolver.release).toHaveBeenCalledWith("late")
    // A duplicate dispose must not double-release.
    md.dispose()
    await flush()
    expect(resolver.release).toHaveBeenCalledTimes(1)
  })

  it("keeps the direct browser URL verbatim", () => {
    const root = document.createElement("div")
    const artifact = "/api/sessions/s1/artifacts/gen/pic.png"
    root.innerHTML = `<img src="${artifact}">`
    const md = createMarkdownMediaResolver(createBrowserMediaResolver())
    md.resolve(root)
    expect(root.querySelector("img").getAttribute("src")).toBe(artifact)
  })

  it("stops the generation watch on dispose so a later fence flip cannot recreate a lease", async () => {
    const root = document.createElement("div")
    root.innerHTML = `<img src="${ARTIFACT}">`
    const generation = ref(0)
    const resolver = hostResolver({
      generation,
      resolveImage: vi.fn(() =>
        Promise.resolve({ url: "vscode-webview://spool/r1", resourceId: "r1" }),
      ),
    })
    const md = createMarkdownMediaResolver(resolver)
    md.resolve(root)
    await flush()
    await flush()
    expect(resolver.resolveImage).toHaveBeenCalledTimes(1)
    // The component unmounts (or clears its content): every lease is released and
    // the watcher must stop. A later fence change on a detached tree must NOT
    // start a fresh Host read whose spooled resource would never be released.
    md.dispose()
    expect(resolver.release).toHaveBeenCalledTimes(1)
    generation.value += 1
    await flush()
    await flush()
    expect(resolver.resolveImage).toHaveBeenCalledTimes(1)
    expect(resolver.release).toHaveBeenCalledTimes(1)
  })

  it("re-arms the generation watch after a fresh resolve following dispose", async () => {
    const first = document.createElement("div")
    first.innerHTML = `<img src="${ARTIFACT}">`
    const second = document.createElement("div")
    second.innerHTML = `<img src="${ARTIFACT}">`
    const generation = ref(0)
    let seq = 0
    const resolver = hostResolver({
      generation,
      resolveImage: vi.fn(() =>
        Promise.resolve({ url: `vscode-webview://spool/${++seq}`, resourceId: `r${seq}` }),
      ),
    })
    const md = createMarkdownMediaResolver(resolver)
    md.resolve(first)
    await flush()
    await flush()
    expect(resolver.resolveImage).toHaveBeenCalledTimes(1)
    // Content is cleared mid-lifetime (dispose), then a new subtree renders.
    md.dispose()
    md.resolve(second)
    await flush()
    await flush()
    expect(resolver.resolveImage).toHaveBeenCalledTimes(2)
    // The re-armed watcher must still invalidate an unchanged reference on a flip.
    generation.value += 1
    await flush()
    await flush()
    expect(resolver.resolveImage).toHaveBeenCalledTimes(3)
    expect(second.querySelector("img").getAttribute("src")).toBe("vscode-webview://spool/3")
  })
})
