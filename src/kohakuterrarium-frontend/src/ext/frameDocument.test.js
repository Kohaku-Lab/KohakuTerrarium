import { describe, expect, it } from "vitest"

import { FRAME_SANDBOX, buildFrameDocument, frameCsp } from "@/ext/frameDocument"
import { FRAME_CLIENT_SOURCE } from "@/ext/sdk/frameBridge"

const ORIGIN = "http://127.0.0.1:8001"
const BASE = `${ORIGIN}/api/ext/alpha/ui/TOKEN/`

function build(html) {
  const out = buildFrameDocument(html, { base: BASE, origin: ORIGIN })
  return { out, doc: new DOMParser().parseFromString(out, "text/html") }
}

describe("frame sandbox and policy", () => {
  it("runs scripts only, with no same-origin, form, popup or top-navigation power", () => {
    expect(FRAME_SANDBOX).toBe("allow-scripts")
  })

  it("allows assets from the asset origin only and blocks network access and nested frames", () => {
    const csp = frameCsp(ORIGIN)
    expect(csp).toContain("default-src 'none'")
    expect(csp).toContain(`script-src 'unsafe-inline' ${ORIGIN}`)
    expect(csp).toContain("connect-src 'none'")
    expect(csp).toContain("frame-src 'none'")
    expect(csp).toContain("object-src 'none'")
    expect(csp).toContain("base-uri 'none'")
    expect(csp).not.toContain("unsafe-eval")
    expect(csp).not.toContain("*")
  })
})

describe("buildFrameDocument", () => {
  it("puts charset, base, policy and client ahead of the package's own head", () => {
    const { doc } = build(
      '<!doctype html><html><head><title>t</title><script src="app.js"></script></head><body><p id="x">hi</p></body></html>',
    )
    const kids = [...doc.head.children].map((el) => el.tagName)
    expect(kids.slice(0, 4)).toEqual(["META", "BASE", "META", "SCRIPT"])
    expect(doc.head.querySelector("base").getAttribute("href")).toBe(BASE)
    expect(doc.head.querySelector('meta[http-equiv="Content-Security-Policy"]').content).toBe(
      frameCsp(ORIGIN),
    )
    expect(doc.head.querySelectorAll("script")[0].textContent).toBe(FRAME_CLIENT_SOURCE)
    expect(doc.querySelector("title").textContent).toBe("t")
    expect(doc.querySelector('script[src="app.js"]')).not.toBeNull()
    expect(doc.getElementById("x").textContent).toBe("hi")
  })

  it("drops the package's own base and policy tags so it cannot loosen the sandbox", () => {
    const { doc } = build(
      '<html><head><base href="https://evil.test/"><meta http-equiv="Content-Security-Policy" content="default-src *"></head><body></body></html>',
    )
    expect(doc.querySelectorAll("base")).toHaveLength(1)
    expect(doc.querySelector("base").getAttribute("href")).toBe(BASE)
    const policies = [...doc.querySelectorAll('meta[http-equiv="Content-Security-Policy"]')]
    expect(policies.map((m) => m.content)).toEqual([frameCsp(ORIGIN)])
  })

  it("wraps a fragment and an empty page into a complete document", () => {
    for (const html of ["<p>just a fragment</p>", "", undefined]) {
      const { out, doc } = build(html)
      expect(out.startsWith("<!doctype html>")).toBe(true)
      expect(doc.head.querySelector("base")).not.toBeNull()
      expect(doc.head.querySelector("script")).not.toBeNull()
    }
  })

  it("keeps a charset the package declared instead of adding a second one", () => {
    const { doc } = build('<html><head><meta charset="latin1"></head><body></body></html>')
    expect(doc.querySelectorAll("meta[charset]")).toHaveLength(1)
  })

  it("does not run the package's scripts while building", () => {
    globalThis.__frameDocumentRan = false
    build("<html><body><script>globalThis.__frameDocumentRan = true</script></body></html>")
    expect(globalThis.__frameDocumentRan).toBe(false)
  })
})
