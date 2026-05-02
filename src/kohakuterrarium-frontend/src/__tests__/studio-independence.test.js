/**
 * Isolation contract: non-studio frontend code must not import
 * from studio subtrees. The only permitted touch point is
 * components/layout/NavRail.vue (T2) which links to /studio.
 *
 * Mirrors tests/unit/test_studio_independence.py on the backend.
 */

import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SRC_ROOT = path.resolve(__dirname, "..")

// Subtrees owned by studio — nothing outside of them may import from them.
const STUDIO_DIRS = [
  path.join(SRC_ROOT, "pages", "studio"),
  path.join(SRC_ROOT, "components", "studio"),
  path.join(SRC_ROOT, "stores", "studio"),
  path.join(SRC_ROOT, "composables", "studio"),
  path.join(SRC_ROOT, "utils", "studio"),
]

// Files permitted to import from studio (touch points)
const ALLOWLIST = new Set([
  // NavRail has one router-link to /studio — string literal, not an import.
  // StudioEditorTab is the v2 macro-shell embed for Studio (sanctioned
  // bridge per the v1/v2 paradigm — Studio pages still own the surface,
  // we just embed them as tabs). See plans/structure-hierarchy/UI/.
  path.join(SRC_ROOT, "components", "shell", "tabs", "StudioEditorTab.vue"),
  // RailGroupQuick reads the studio workspace store solely to decide
  // which Studio tab to open from the rail (workspace dashboard if a
  // workspace is open, picker otherwise). Read-only consumer; no
  // mutation of studio state. Same sanctioned-bridge category as
  // StudioEditorTab above.
  path.join(SRC_ROOT, "components", "shell", "RailGroupQuick.vue"),
])

const STUDIO_IMPORT_PATTERNS = [
  /from\s+["']@\/pages\/studio\//,
  /from\s+["']@\/components\/studio\//,
  /from\s+["']@\/stores\/studio\//,
  /from\s+["']@\/composables\/studio\//,
  /from\s+["']@\/utils\/studio\//,
  /import\s+["']@\/pages\/studio\//,
  /import\s+["']@\/components\/studio\//,
  /import\s+["']@\/stores\/studio\//,
  /import\s+["']@\/composables\/studio\//,
  /import\s+["']@\/utils\/studio\//,
]

function isInsideStudio(filePath) {
  return STUDIO_DIRS.some((dir) => filePath.startsWith(dir + path.sep))
}

function walkSource(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name.startsWith(".")) continue
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      walkSource(full, out)
    } else if (/\.(vue|js|mjs|ts)$/.test(entry.name)) {
      out.push(full)
    }
  }
  return out
}

describe("studio isolation contract", () => {
  it("no non-studio file imports from studio/**", () => {
    const offenders = []
    for (const file of walkSource(SRC_ROOT)) {
      if (isInsideStudio(file)) continue
      if (ALLOWLIST.has(file)) continue
      const text = fs.readFileSync(file, "utf-8")
      for (const rx of STUDIO_IMPORT_PATTERNS) {
        if (rx.test(text)) {
          offenders.push({
            file: path.relative(SRC_ROOT, file),
            pattern: rx.source,
          })
          break
        }
      }
    }
    if (offenders.length) {
      const msg = offenders.map((o) => `  ${o.file}: matched ${o.pattern}`).join("\n")
      throw new Error(
        `Studio imports leaked into runner code:\n${msg}\n\n` +
          "If you genuinely need this, amend plans/kt-studio/README.md §1 first.",
      )
    }
    expect(offenders).toEqual([])
  })

  it("studio subtrees actually exist", () => {
    for (const dir of STUDIO_DIRS) {
      expect(fs.existsSync(dir), `studio subtree missing: ${path.relative(SRC_ROOT, dir)}`).toBe(
        true,
      )
    }
  })

  it("NavRail.vue links to /studio (touch point T2)", () => {
    const navRail = path.join(SRC_ROOT, "components", "layout", "NavRail.vue")
    const text = fs.readFileSync(navRail, "utf-8")
    expect(text.includes('to="/studio"')).toBe(true)
  })
})
