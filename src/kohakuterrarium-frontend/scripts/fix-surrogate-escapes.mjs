#!/usr/bin/env node
/**
 * Post-build fix-up for rolldown-vite surrogate-escape corruption.
 *
 * rolldown-vite 7.3.x corrupts JavaScript source files that contain lone
 * surrogate escapes like `\uD800`. It reads the escape into a Rust string,
 * which can't hold lone surrogates, so the surrogate codepoint gets
 * replaced with U+FFFD. The original escape text (`d800`, etc.) then
 * appears next to the replacement character in the output, producing
 * literal byte sequences like `<U+FFFD>d800` where `\uD800` should be.
 *
 * This breaks KaTeX's token regex specifically — KaTeX uses
 * `[\uD800-\uDBFF][\uDC00-\uDFFF]` to match surrogate pairs. After
 * corruption, that character class degenerates into something like
 * `[<U+FFFD>d800-<U+FFFD>dbff]` which matches most ASCII — including
 * `\i`, `\m`, `\b` — so every `\command` in display math gets tokenized
 * as a bogus two-character control sequence and fails to parse.
 *
 * The fix: walk every .js file under the build output and replace
 * `<U+FFFD><hex4 starting with d>` with `\uXXXX` escapes. The replacement
 * is safe because U+FFFD followed by exactly four hex digits starting
 * with `d` is extremely unlikely to occur legitimately.
 */

import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const outDir = path.resolve(__dirname, "../../kohakuterrarium/web_dist")

const CORRUPTED = /\uFFFD([dD][0-9a-fA-F]{3})/g

function walk(dir) {
  const entries = fs.readdirSync(dir, { withFileTypes: true })
  for (const entry of entries) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      walk(full)
    } else if (entry.isFile() && entry.name.endsWith(".js")) {
      const buf = fs.readFileSync(full)
      const text = buf.toString("utf-8")
      if (!text.includes("\uFFFD")) continue
      let fixed = 0
      const patched = text.replace(CORRUPTED, (_m, hex) => {
        fixed += 1
        return "\\u" + hex
      })
      if (fixed > 0) {
        fs.writeFileSync(full, patched, "utf-8")
        const rel = path.relative(outDir, full)
        console.log(`  patched ${fixed} escape(s) in ${rel}`)
      }
    }
  }
}

if (!fs.existsSync(outDir)) {
  console.error(`[fix-surrogate-escapes] build output not found: ${outDir}`)
  process.exit(1)
}

console.log(`[fix-surrogate-escapes] scanning ${outDir}`)
walk(outDir)
console.log("[fix-surrogate-escapes] done")
