import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import MarkdownIt from "markdown-it"

import { applyExternalLinkRule, installExternalLinkGuard, isExternalUrl } from "./externalLinks"

// jsdom serves the app from http://localhost/ (see vitest.config.js).
const SAME_ORIGIN = "http://localhost"

describe("isExternalUrl", () => {
  it("treats other http(s) origins as external", () => {
    expect(isExternalUrl("https://github.com/Kohaku-Lab/KohakuTerrarium")).toBe(true)
    expect(isExternalUrl("http://example.test/docs")).toBe(true)
    expect(isExternalUrl("//example.test/docs")).toBe(true)
  })

  it("keeps same-origin navigation inside the app", () => {
    expect(isExternalUrl("/sessions/abc")).toBe(false)
    expect(isExternalUrl("#anchor")).toBe(false)
    expect(isExternalUrl(`${SAME_ORIGIN}/sessions/abc`)).toBe(false)
  })

  it("keeps relative navigation internal when origin is unavailable", () => {
    expect(isExternalUrl("/sessions/abc", null)).toBe(false)
    expect(isExternalUrl("sessions/abc", null)).toBe(false)
  })

  it("ignores schemes the webview already hands to the OS", () => {
    expect(isExternalUrl("mailto:kohaku@example.test")).toBe(false)
    expect(isExternalUrl("tel:+123")).toBe(false)
    expect(isExternalUrl("data:text/plain,hi")).toBe(false)
    expect(isExternalUrl("javascript:alert(1)")).toBe(false)
  })

  it("returns false for empty and unparseable hrefs", () => {
    expect(isExternalUrl("")).toBe(false)
    expect(isExternalUrl(null)).toBe(false)
    expect(isExternalUrl("http://[unbalanced")).toBe(false)
  })
})

describe("applyExternalLinkRule", () => {
  function render(markdown) {
    const md = applyExternalLinkRule(new MarkdownIt({ html: false, linkify: true }))
    return md.render(markdown)
  }

  it("marks external markdown links _blank", () => {
    const html = render("[docs](https://example.test/docs)")

    expect(html).toContain('href="https://example.test/docs"')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
  })

  it("marks linkified bare URLs too", () => {
    // The common case: a model writes a URL in prose, linkify makes it an
    // anchor, and nothing was there to give it a target.
    const html = render("see https://example.test/page for details")

    expect(html).toContain('target="_blank"')
  })

  it("leaves same-origin links untargeted so they stay in the SPA", () => {
    const html = render("[session](/sessions/abc)")

    expect(html).toContain('href="/sessions/abc"')
    expect(html).not.toContain("target=")
  })

  it("runs after an already-installed link_open rule instead of replacing it", () => {
    const md = new MarkdownIt()
    md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
      tokens[idx].attrSet("data-plugin", "kept")
      return self.renderToken(tokens, idx, options, env, self)
    }
    applyExternalLinkRule(md)

    const html = md.render("[docs](https://example.test/docs)")

    expect(html).toContain('data-plugin="kept"')
    expect(html).toContain('target="_blank"')
  })
})

describe("installExternalLinkGuard", () => {
  let uninstall
  let opened

  beforeEach(() => {
    opened = []
    vi.spyOn(window, "open").mockImplementation((url, target, features) => {
      opened.push({ url, target, features })
      return null
    })
    uninstall = installExternalLinkGuard()
  })

  afterEach(() => {
    uninstall()
    document.body.innerHTML = ""
    vi.restoreAllMocks()
  })

  function clickAnchor(html, init = {}) {
    document.body.innerHTML = html
    const anchor = document.querySelector("a")
    const event = new window.MouseEvent("click", {
      bubbles: true,
      cancelable: true,
      button: 0,
      ...init,
    })
    anchor.dispatchEvent(event)
    return event
  }

  it("diverts an untargeted external link to the browser", () => {
    const event = clickAnchor('<a href="https://example.test/docs">docs</a>')

    expect(event.defaultPrevented).toBe(true)
    expect(opened).toEqual([
      { url: "https://example.test/docs", target: "_blank", features: "noopener,noreferrer" },
    ])
  })

  it("resolves protocol-relative hrefs before opening them", () => {
    clickAnchor('<a href="//example.test/docs">docs</a>')

    expect(opened[0].url).toBe("http://example.test/docs")
  })

  it("diverts a click that lands on a child of the anchor", () => {
    document.body.innerHTML = '<a href="https://example.test/docs"><span id="inner">docs</span></a>'
    const event = new window.MouseEvent("click", { bubbles: true, cancelable: true, button: 0 })
    document.getElementById("inner").dispatchEvent(event)

    expect(event.defaultPrevented).toBe(true)
    expect(opened).toHaveLength(1)
  })

  it("leaves same-origin links alone", () => {
    const event = clickAnchor('<a href="/sessions/abc">session</a>')

    expect(event.defaultPrevented).toBe(false)
    expect(opened).toEqual([])
  })

  it("leaves anchors that already carry a target to the platform", () => {
    // pywebview's own new-window handlers already route these correctly;
    // re-opening them here would just add a second path to maintain.
    const event = clickAnchor('<a href="https://example.test/docs" target="_blank">docs</a>')

    expect(event.defaultPrevented).toBe(false)
    expect(opened).toEqual([])
  })

  it("still handles an explicit target=_self", () => {
    const event = clickAnchor('<a href="https://example.test/docs" target="_self">docs</a>')

    expect(event.defaultPrevented).toBe(true)
    expect(opened).toHaveLength(1)
  })

  it("lets modified clicks keep their native new-tab meaning", () => {
    for (const init of [
      { ctrlKey: true },
      { metaKey: true },
      { shiftKey: true },
      { altKey: true },
      { button: 1 },
    ]) {
      const event = clickAnchor('<a href="https://example.test/docs">docs</a>', init)
      expect(event.defaultPrevented).toBe(false)
    }

    expect(opened).toEqual([])
  })

  it("ignores clicks that are not on an anchor", () => {
    document.body.innerHTML = "<button id='btn'>go</button>"
    const event = new window.MouseEvent("click", { bubbles: true, cancelable: true, button: 0 })
    document.getElementById("btn").dispatchEvent(event)

    expect(event.defaultPrevented).toBe(false)
    expect(opened).toEqual([])
  })

  it("stops diverting once uninstalled", () => {
    uninstall()

    const event = clickAnchor('<a href="https://example.test/docs">docs</a>')

    expect(event.defaultPrevented).toBe(false)
    expect(opened).toEqual([])
  })
})
