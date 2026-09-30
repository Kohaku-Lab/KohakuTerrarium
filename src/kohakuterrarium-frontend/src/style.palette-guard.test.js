import { readdirSync, readFileSync, statSync } from "node:fs"
import { join, relative } from "node:path"

import { describe, expect, it } from "vitest"

import unoConfig from "../uno.config.js"

const SRC = "src"
const SKIP_DIRS = new Set(["node_modules", "locales"])
const GEM_NAMES = Object.keys(unoConfig.theme.colors).filter(
  (name) => typeof unoConfig.theme.colors[name] === "object",
)
const UTILITY = "(?:text|bg|border|ring|from|to|via|fill|stroke|divide|outline|placeholder|accent)"
const DEFAULT_PALETTE =
  "(?:red|blue|green|yellow|orange|purple|pink|indigo|violet|gray|grey|slate|zinc|stone|neutral|cyan|sky|lime|rose|fuchsia|amber|emerald|teal)"

const BANNED = [
  {
    reason: "gem colours only have light / DEFAULT / shadow shades",
    pattern: new RegExp(
      `${UTILITY}-(?:${GEM_NAMES.join("|")})-(?:dark|deep|darker)(?![\\w-])`,
      "g",
    ),
  },
  {
    reason: "colour name is not in the gem theme",
    pattern: new RegExp(`(?<![\\w-])${UTILITY}-(?:teal|emerald)(?:-[a-z]+)?(?![\\w-])`, "g"),
  },
  {
    reason: "default Tailwind palette scale instead of gem / warm colours",
    pattern: new RegExp(`(?<![\\w-])${UTILITY}-${DEFAULT_PALETTE}-\\d{2,3}\\b`, "g"),
  },
  {
    reason: "off-palette blue; the primary accent is iolite",
    pattern: /rgba?\(\s*90,\s*140,\s*200|#5a8cc8/gi,
  },
  {
    reason: "off-palette red; danger is the coral gem",
    pattern: /rgba?\(\s*231,\s*76,\s*60/g,
  },
  {
    reason: "ad-hoc warm grey; use the warm-500 theme variable",
    pattern: /rgba?\(\s*120,\s*109,\s*98/g,
  },
]

// Files that may name colours outside the gem theme, with the reason.
const ALLOWED = new Map([
  ["composables/useAttentionEffects.js", "favicon SVG data URLs cannot use utility classes"],
])

function sourceFiles(dir) {
  const found = []
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) {
      if (!SKIP_DIRS.has(name)) found.push(...sourceFiles(path))
    } else if (/\.(vue|js|css)$/.test(name) && !/\.test\.js$/.test(name)) {
      found.push(path)
    }
  }
  return found
}

describe("palette guard", () => {
  it("uses only gem and warm colours in application source", () => {
    const offenders = []
    for (const file of sourceFiles(SRC)) {
      const name = relative(SRC, file)
      if (ALLOWED.has(name)) continue
      const text = readFileSync(file, "utf8")
      for (const { pattern, reason } of BANNED) {
        for (const match of text.matchAll(pattern)) {
          const line = text.slice(0, match.index).split("\n").length
          offenders.push(`${name}:${line} ${match[0]} (${reason})`)
        }
      }
    }
    expect(offenders).toEqual([])
  })

  it("defines every btn-* shortcut the source uses", () => {
    const defined = new Set(Object.keys(unoConfig.shortcuts))
    const missing = []
    for (const file of sourceFiles(SRC)) {
      const text = readFileSync(file, "utf8")
      for (const match of text.matchAll(/(?<![\w:-])btn-[a-z][a-z-]*(?![\w-])/g)) {
        if (!defined.has(match[0])) missing.push(`${relative(SRC, file)} ${match[0]}`)
      }
    }
    expect(missing).toEqual([])
  })

  it("keeps every allowlist entry pointing at a real file", () => {
    for (const name of ALLOWED.keys()) {
      expect(() => statSync(join(SRC, name))).not.toThrow()
    }
  })
})
