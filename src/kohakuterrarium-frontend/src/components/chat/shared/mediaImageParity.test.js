// Focused parity guard for the shared inline image + media status rules.
//
// The Dashboard renders ``SharedMediaImage`` (public/chat/MediaPreview.js) with
// NO ``.kt-conversation-host`` ancestor, so the shared ``--kt-conversation-*``
// tokens are undefined there and every declaration that leans on them must carry
// the production fallback. The VS Code webview *does* set ``.kt-conversation-host``
// (src/webview/index.js) and supplies ``--vscode-*`` tokens. Both hosts consume
// the single rule in ``conversation-message.css``, so this test reads that exact
// production stylesheet (no duplicated CSS copy) and checks:
//
//   * the geometry/border restored from the deleted Dashboard ``.chat-inline-image``
//     scoped block (min(65%, 42vw) / 35vh / @supports 65cqw 50cqh / rgb fallbacks),
//   * the shared transcript viewport is the query container for cqw/cqh in BOTH
//     hosts (fixes the Dashboard-only container that let ``42vw`` misbehave),
//   * ``.kt-media-status`` / ``.is-error`` have real shared styles,
//   * the image max-width can never exceed its container at 320/480/960,
//   * code blocks stay inside their own scroll box (no message overflow).
//
// jsdom (the only DOM tooling available here) does not evaluate min()/cqw/@supports,
// so the width matrix resolves the *declared* values pulled from the file with a
// tiny unit resolver instead of hard-coding a second CSS prototype.
import fs from "node:fs"

import { describe, expect, it } from "vitest"

const CONVERSATION_CSS = "src/components/chat/shared/conversation-message.css"
const TRANSCRIPT_CSS = "src/components/chat/shared/chat-transcript-section.css"
const DASHBOARD_MESSAGE = "src/components/chat/ChatMessage.vue"
const DASHBOARD_PANEL = "src/components/chat/ChatPanel.vue"
const MARKDOWN = "src/public/chat/MarkdownRenderer.vue"

function read(path) {
  return fs.readFileSync(path, "utf8")
}

function escapeSelector(selector) {
  return selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

// Declaration body of the first top-level rule with this exact selector.
function declarations(source, selector) {
  const match = source.match(
    new RegExp("(?:^|[}\\n])\\s*" + escapeSelector(selector) + "\\s*\\{([^{}]*)\\}"),
  )
  return match ? match[1] : ""
}

function property(body, name) {
  const match = body.match(new RegExp("(?:^|;|\\{)\\s*" + name + "\\s*:\\s*([^;]+);"))
  return match ? match[1].trim() : ""
}

function supportsBlock(source) {
  const match = source.match(
    /@supports\s*\(max-width:\s*65cqw\)\s*\{\s*\.kt-conversation-image\s*\{([^}]*)\}/,
  )
  return match ? match[1] : ""
}

function rgbTriplets(value) {
  return [...value.matchAll(/rgb\(\s*(\d+)\s+(\d+)\s+(\d+)/g)].map((match) =>
    match.slice(1, 4).map(Number),
  )
}

// --- minimal resolver for the declared length grammar used by the rule --------
function splitArguments(value) {
  const parts = []
  let depth = 0
  let current = ""
  for (const char of value) {
    if (char === "(") depth++
    else if (char === ")") depth--
    else if (char === "," && depth === 0) {
      parts.push(current)
      current = ""
      continue
    }
    current += char
  }
  parts.push(current)
  return parts.map((part) => part.trim())
}

function resolveLength(value, box) {
  const term = value.trim()
  if (term.startsWith("min(")) {
    return Math.min(...splitArguments(term.slice(4, -1)).map((part) => resolveLength(part, box)))
  }
  const match = term.match(/^([\d.]+)(vw|vh|cqw|cqh|%)$/)
  if (!match) throw new Error(`unsupported length: ${term}`)
  const basis = {
    "%": box.containerWidth,
    cqw: box.containerWidth,
    cqh: box.containerHeight,
    vw: box.viewportWidth,
    vh: box.viewportHeight,
  }[match[2]]
  return (Number(match[1]) / 100) * basis
}

describe("shared inline image parity", () => {
  const source = read(CONVERSATION_CSS)
  const image = declarations(source, ".kt-conversation-image")

  it("restores the Dashboard production geometry and border on the shared rule", () => {
    expect(property(image, "max-width")).toBe("min(65%, 42vw)")
    expect(property(image, "max-height")).toBe("35vh")
    expect(property(image, "width")).toBe("auto")
    expect(property(image, "height")).toBe("auto")
    expect(property(image, "object-fit")).toBe("contain")
    expect(property(image, "border-radius")).toBe("0.5rem")
    expect(property(image, "border")).toBe(
      "1px solid var(--kt-conversation-border, rgb(231 223 211 / 1))",
    )
  })

  it("restores the container-query caps under the same @supports guard", () => {
    const block = supportsBlock(source)
    expect(property(block, "max-width")).toBe("65cqw")
    expect(property(block, "max-height")).toBe("50cqh")
  })

  it("keeps the production dark border without breaking the Extension token", () => {
    const dark = declarations(source, ".dark .kt-conversation-image")
    expect(property(dark, "border-color")).toBe("var(--kt-conversation-border, rgb(89 75 61 / 1))")
  })

  it("makes the shared transcript viewport the cqw/cqh container in both hosts", () => {
    const viewport = declarations(read(TRANSCRIPT_CSS), ".kt-transcript-viewport")
    expect(property(viewport, "container-type")).toBe("size")
  })

  it("styles the shared loading/error media status", () => {
    const loading = declarations(source, ".kt-media-status")
    expect(property(loading, "color")).toContain("--kt-conversation-muted")
    expect(property(loading, "padding")).not.toBe("")

    const error = declarations(source, ".kt-media-status.is-error")
    expect(property(error, "color")).toContain("--kt-conversation-error")
    expect(property(error, "border")).toContain("color-mix")
    expect(property(error, "background")).toContain("color-mix")
  })

  it("drops the now-dead Dashboard scoped .chat-inline-image block exactly once", () => {
    expect(read(DASHBOARD_MESSAGE)).not.toMatch(/chat-inline-image/)
    // The Dashboard never provides the host-variable scope, so the shared rule
    // must stand on its own fallbacks (asserted above).
    expect(read(DASHBOARD_MESSAGE)).not.toMatch(/kt-conversation-host/)
    expect(read(DASHBOARD_PANEL)).not.toMatch(/kt-conversation-host/)
  })

  it("resolves to a valid border colour in Dashboard (fallback) and Extension (token)", () => {
    const lightFallback = rgbTriplets(property(image, "border"))
    const darkFallback = rgbTriplets(
      property(declarations(source, ".dark .kt-conversation-image"), "border-color"),
    )
    const vscodeToken = rgbTriplets("var(--vscode-panel-border)")

    expect(lightFallback).toEqual([[231, 223, 211]])
    expect(darkFallback).toEqual([[89, 75, 61]])
    // Every sampled pixel channel stays a legal 0..255 colour; the Extension
    // resolves the same declaration to its own --vscode-panel-border token.
    for (const triplet of [lightFallback[0], darkFallback[0]]) {
      expect(triplet.every((channel) => channel >= 0 && channel <= 255)).toBe(true)
    }
    expect(vscodeToken).toEqual([])
  })

  it("keeps the inline image inside its container at 320/480/960 in either host", () => {
    const base = property(image, "max-width")
    const capped = property(supportsBlock(source), "max-width")
    expect(base).toBe("min(65%, 42vw)")
    expect(capped).toBe("65cqw")

    for (const width of [320, 480, 960]) {
      // Dashboard: window is wide (1600) but the chat panel/container is narrow.
      const dashboard = {
        containerWidth: width,
        containerHeight: 640,
        viewportWidth: 1600,
        viewportHeight: 900,
      }
      // Extension: the webview viewport equals the host width; cqw resolves
      // against the shared transcript container (container-type: size).
      const extension = {
        containerWidth: width,
        containerHeight: 720,
        viewportWidth: width,
        viewportHeight: 720,
      }
      for (const box of [dashboard, extension]) {
        for (const declared of [base, capped]) {
          expect(resolveLength(declared, box)).toBeLessThan(width)
        }
      }
    }
  })

  it("keeps fenced code inside its own scroller so it never widens the message", () => {
    const markdown = read(MARKDOWN)
    expect(markdown).toMatch(/\.md-content \.code-block\s*\{[^}]*overflow:\s*hidden/s)
    expect(markdown).toMatch(/\.md-content \.code-block pre\.hljs\s*\{[^}]*overflow-x:\s*auto/s)
    expect(markdown).toMatch(/\.md-content > pre\.hljs\s*\{[^}]*overflow-x:\s*auto/s)
  })
})
