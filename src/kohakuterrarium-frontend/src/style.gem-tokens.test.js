import { readFileSync } from "node:fs"

import { describe, expect, it } from "vitest"

import unoConfig from "../uno.config.js"

const THEME_COLORS = unoConfig.theme.colors
const SHARED_CSS = [
  "src/components/chat/shared/conversation-message.css",
  "src/components/chat/shared/chat-transcript-section.css",
  "src/components/chat/chat-panel.css",
  "src/public/chat/chat-composer.css",
]

function flattenTheme() {
  const flat = {}
  for (const [name, value] of Object.entries(THEME_COLORS)) {
    if (typeof value === "string") flat[name] = value.toLowerCase()
    else
      for (const [shade, hex] of Object.entries(value))
        flat[shade === "DEFAULT" ? name : `${name}-${shade}`] = hex.toLowerCase()
  }
  return flat
}

function preflightCss() {
  const [preflight] = unoConfig.preflights
  return preflight.getCSS({ theme: unoConfig.theme })
}

describe("gem colour variables", () => {
  it("emits one root variable per theme colour, generated from uno.config.js", () => {
    const css = preflightCss()
    for (const [name, hex] of Object.entries(flattenTheme())) {
      expect(css.toLowerCase()).toContain(`--kt-color-${name}: ${hex};`)
    }
    expect(css.startsWith(":root {")).toBe(true)
  })

  it("keeps every hex fallback in shared chat css equal to its named theme colour", () => {
    const theme = flattenTheme()
    for (const file of SHARED_CSS) {
      const css = readFileSync(file, "utf8")
      for (const [, name, hex] of css.matchAll(
        /var\(--kt-color-([a-z0-9-]+),\s*(#[0-9a-fA-F]{3,8})\)/g,
      )) {
        expect(theme[name], `${file}: unknown colour --kt-color-${name}`).toBeDefined()
        expect(hex.toLowerCase(), `${file}: fallback drift for ${name}`).toBe(theme[name])
      }
    }
  })

  it("declares no raw palette colour in shared chat css outside a var() fallback", () => {
    const allowed = new Set(["#fff", "#ffffff", ...Object.values(flattenTheme())])
    for (const file of SHARED_CSS) {
      const css = readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "")
      const withoutFallbacks = css.replace(/var\([^()]*?,\s*#[0-9a-fA-F]{3,8}\)/g, "var(...)")
      const rawHex = withoutFallbacks.match(/#[0-9a-fA-F]{3,8}\b/g) || []
      const offenders = rawHex.filter((hex) => !["#fff", "#ffffff"].includes(hex.toLowerCase()))
      expect(offenders, `${file}: raw hex outside var() fallback`).toEqual([])
      for (const [, hex] of css.matchAll(/var\([^()]*?,\s*(#[0-9a-fA-F]{3,8})\)/g)) {
        expect(
          allowed.has(hex.toLowerCase()),
          `${file}: fallback ${hex} is not a theme colour`,
        ).toBe(true)
      }
    }
  })

  it("declares no custom rgb colour in the style block of the shared message row", () => {
    const source = readFileSync("src/components/chat/shared/MessageRow.vue", "utf8")
    const styles = [...source.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1])
    expect(styles.length).toBeGreaterThan(0)
    const rgbs = styles
      .join("\n")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .match(/rgb\((?!\s*0\s+0\s+0)[^)]*\)/g)
    expect(rgbs || []).toEqual([])
  })

  it("declares no stone-scale or custom rgb colour in shared chat css", () => {
    // Production image-border fallbacks pinned by mediaImageParity.test.js.
    const pinned = new Set(["rgb(231 223 211 / 1)", "rgb(89 75 61 / 1)"])
    for (const file of SHARED_CSS) {
      const css = readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "")
      const rgbs = (css.match(/rgb\((?!\s*0\s+0\s+0)[^)]*\)/g) || []).filter((v) => !pinned.has(v))
      expect(rgbs, `${file}: raw rgb() colour`).toEqual([])
    }
  })
})
