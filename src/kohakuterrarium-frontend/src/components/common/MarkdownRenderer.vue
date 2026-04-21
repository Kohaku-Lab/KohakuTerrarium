<template>
  <div ref="rootEl" class="md-content" @click="onClick" v-html="rendered" />
</template>

<script setup>
import { onBeforeUnmount, ref, shallowRef, watch } from "vue"
import MarkdownIt from "markdown-it"
import markdownItKatex from "@vscode/markdown-it-katex"
import hljs from "highlight.js"

const props = defineProps({
  content: { type: String, default: "" },
})

const rootEl = ref(null)

const md = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: false,
  highlight(str, lang) {
    const displayLang = lang || "text"
    const langClass = lang && hljs.getLanguage(lang) ? lang : ""
    let highlighted
    if (langClass) {
      try {
        highlighted = hljs.highlight(str, { language: lang }).value
      } catch {
        highlighted = md.utils.escapeHtml(str)
      }
    } else {
      highlighted = md.utils.escapeHtml(str)
    }
    // Wrap with header (language label + copy button)
    return `<div class="code-block">` + `<div class="code-header">` + `<span class="code-lang">${displayLang}</span>` + `<button class="code-copy-btn" data-copy="${md.utils.escapeHtml(str).replace(/"/g, "&quot;")}" title="Copy">Copy</button>` + `</div>` + `<pre class="hljs"><code>${highlighted}</code></pre>` + `</div>`
  },
})

const katexPlugin = typeof markdownItKatex === "function" ? markdownItKatex : markdownItKatex?.default
if (typeof katexPlugin === "function") {
  md.use(katexPlugin)
}

function onClick(e) {
  const btn = e.target.closest(".code-copy-btn")
  if (!btn) return
  const raw = btn.getAttribute("data-copy") || ""
  // Decode HTML entities
  const decoded = new DOMParser().parseFromString(raw, "text/html").body.textContent
  navigator.clipboard.writeText(decoded || "").then(() => {
    const orig = btn.textContent
    btn.textContent = "Copied!"
    btn.classList.add("copied")
    setTimeout(() => {
      btn.textContent = orig
      btn.classList.remove("copied")
    }, 1500)
  })
}

/*
 * Invisible / formatting / combining characters that can silently split
 * KaTeX control sequences. Without stripping, `\int` with any of these
 * inserted between `\i` and `nt` tokenizes as `\i` (undefined) + text.
 * None of these are ever semantically meaningful in chat markdown, so
 * we strip globally rather than per-math-block.
 *
 * `\p{Cf}` covers the entire Unicode Format category (soft hyphen,
 * bidi controls, zero-width joiners, word joiner, BOM, deprecated
 * format chars, Arabic/Mongolian/Kaithi/Egyptian/Duployan/Musical
 * format chars, language tags, …). Explicit ranges cover a few
 * problematic codepoints outside Cf:
 * - U+0332             COMBINING LOW LINE (KaTeX's error-display underline)
 * - U+034F             COMBINING GRAPHEME JOINER
 * - U+115F, U+1160     HANGUL CHOSEONG / JUNGSEONG FILLER
 * - U+17B4, U+17B5     KHMER VOWEL INHERENT AQ / AA
 * - U+3164             HANGUL FILLER
 * - U+FE00-U+FE0F      Variation selectors (Mn, not Cf)
 */
const INVISIBLE_CHARS = /[\u0332\u034F\u115F\u1160\u17B4\u17B5\u3164\uFE00-\uFE0F]|\p{Cf}/gu

/*
 * Pre-process content to normalize LaTeX delimiters:
 * - \( ... \) -> $ ... $  (inline)
 * - \[ ... \] -> $$ ... $$ (block, on own lines)
 * - Ensure $$ blocks have blank lines around them
 * Also strips invisible characters that break KaTeX tokenization.
 */
function preprocessLatex(text) {
  if (!text) return ""

  // Normalize CRLF to LF, then strip any remaining bare CR. A stray \r
  // mid-word (e.g. between \i and nt from a bad stream chunk) splits
  // KaTeX control-sequence tokenization just like invisible chars do.
  text = text.replace(/\r\n/g, "\n").replace(/\r/g, "")

  // Strip invisible / formatting / combining chars (see above).
  text = text.replace(INVISIBLE_CHARS, "")

  // Convert \( ... \) to $ ... $ for inline math
  text = text.replace(/\\\((.+?)\\\)/g, (_, math) => `$${math}$`)

  // Convert \[ ... \] to $$ ... $$ for block math (may span lines)
  text = text.replace(/\\\[([\s\S]*?)\\\]/g, (_, math) => `\n$$${math.trim()}$$\n`)

  // Ensure $$ blocks are surrounded by blank lines for proper parsing
  text = text.replace(/([^\n])(\n\$\$)/g, "$1\n$2")
  text = text.replace(/(\$\$\n)([^\n])/g, "$1\n$2")

  return text
}

// Catches both inline (<span class="katex-error">) and block
// (<p class="katex-block katex-error">) error wrappers emitted by the
// @vscode/markdown-it-katex plugin.
const KATEX_ERROR_RE = /<(span|p) class="(?:katex-block )?katex-error" title="([^"]*)">([\s\S]*?)<\/\1>/g

function decodeHtmlEntities(s) {
  return s
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#039;/g, "'")
}

function renderMarkdown(content) {
  try {
    const html = md.render(content)
    // Replace KaTeX error markers with a compact code-style fallback so
    // the user sees the raw LaTeX, not the parse-error message. The
    // actual error is emitted to the console for debugging.
    return html.replace(KATEX_ERROR_RE, (_match, _tag, titleHtml, bodyHtml) => {
      const latex = decodeHtmlEntities(titleHtml)
      const msg = decodeHtmlEntities(bodyHtml.replace(/<[^>]+>/g, ""))
      // Dump char codes of the first 30 chars — makes it trivial to spot
      // any invisible char that's slipping past the INVISIBLE_CHARS filter.
      const codes = Array.from(latex.slice(0, 30))
        .map((c) => `${c === "\n" ? "\\n" : c === "\r" ? "\\r" : c}(0x${c.codePointAt(0).toString(16).toUpperCase()})`)
        .join(" ")
      console.warn(`[MarkdownRenderer] KaTeX parse error for ${JSON.stringify(latex)}: ${msg}\n  chars: ${codes}`)
      // titleHtml is already HTML-escaped so it's safe to embed as-is.
      return `<code class="katex-fallback">${titleHtml}</code>`
    })
  } catch (err) {
    console.warn("[MarkdownRenderer] markdown-it render failed, falling back to escaped text:", err)
    return md.render(content.replace(/\$/g, "\\$"))
  }
}

/*
 * Throttled rendering.
 *
 * `md.render` + `hljs.highlight` is expensive and grows with content
 * length — running it on every streaming chunk (~30 fps) saturates
 * both CPU and GPU (the latter because `v-html` swaps the whole
 * subtree, invalidating every code-block compositor layer).
 *
 * We coalesce rapid updates: the last-requested render runs at most
 * once per THROTTLE_MS, plus a guaranteed trailing render so the
 * final content is always correct. A brand-new mount / cleared
 * content renders synchronously to avoid a flicker of empty output.
 *
 * `shallowRef` avoids deep reactivity on a value that's always
 * replaced whole.
 */
const rendered = shallowRef("")

const THROTTLE_MS = 80 // ~12 fps during streaming; user eye can't tell

let lastRenderAt = 0
let pendingTimer = null
let pendingContent = null

function doRender(content) {
  if (!content) {
    rendered.value = ""
    return
  }
  rendered.value = renderMarkdown(preprocessLatex(content))
  lastRenderAt = performance.now()
}

function scheduleRender(content) {
  pendingContent = content
  if (pendingTimer !== null) return // trailing render will pick up latest content
  const now = performance.now()
  const elapsed = now - lastRenderAt
  if (elapsed >= THROTTLE_MS) {
    // Fast path: cooldown elapsed, render immediately.
    doRender(pendingContent)
    pendingContent = null
    return
  }
  // Schedule trailing render at the end of the cooldown window.
  pendingTimer = setTimeout(() => {
    pendingTimer = null
    const c = pendingContent
    pendingContent = null
    if (c != null) doRender(c)
  }, THROTTLE_MS - elapsed)
}

// Immediate render on first content so the component never mounts empty.
if (props.content) doRender(props.content)

watch(
  () => props.content,
  (content) => scheduleRender(content),
)

onBeforeUnmount(() => {
  if (pendingTimer !== null) {
    clearTimeout(pendingTimer)
    pendingTimer = null
  }
})
</script>

<style>
@import "katex/dist/katex.min.css";

.md-content {
  line-height: 1.65;
  word-wrap: break-word;
}

.md-content img {
  display: block;
  max-width: min(65%, 42vw);
  max-height: 35vh;
  width: auto;
  height: auto;
  object-fit: contain;
  border-radius: 0.5rem;
  margin: 0.5rem 0;
}

@supports (max-width: 65cqw) {
  .md-content img {
    max-width: 65cqw;
    max-height: 50cqh;
  }
}
.md-content p {
  margin-bottom: 0.5em;
}
.md-content p:last-child {
  margin-bottom: 0;
}
.md-content h1,
.md-content h2,
.md-content h3,
.md-content h4,
.md-content h5,
.md-content h6 {
  margin-top: 0.8em;
  margin-bottom: 0.4em;
  font-weight: 600;
  color: var(--color-text);
}
.md-content h1 {
  font-size: 1.3em;
}
.md-content h2 {
  font-size: 1.15em;
}
.md-content h3 {
  font-size: 1.05em;
}
.md-content ul,
.md-content ol {
  margin: 0.4em 0;
  padding-left: 1.5em;
}
.md-content li {
  margin-bottom: 0.2em;
}
.md-content li p {
  margin-bottom: 0.2em;
}
.md-content code {
  background: rgba(0, 0, 0, 0.06);
  padding: 0.15em 0.35em;
  border-radius: 4px;
  font-size: 0.9em;
  font-family: var(--font-mono);
}
html.dark .md-content code {
  background: rgba(255, 255, 255, 0.08);
}
/* KaTeX parse-error fallback — show raw LaTeX in a subdued tone so the
 * user isn't confronted with a red parse-error block. Real errors go to
 * the dev console. */
.md-content code.katex-fallback {
  background: rgba(165, 126, 174, 0.08);
  color: var(--color-text-muted);
  border: 1px dashed rgba(165, 126, 174, 0.35);
  white-space: pre-wrap;
}
.md-content .code-block {
  margin: 0.6em 0;
  border-radius: 8px;
  overflow: hidden;
  background: #1a1a2e;
}
html.dark .md-content .code-block {
  background: #0d0d1a;
}
.md-content .code-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.35em 0.8em;
  background: rgba(255, 255, 255, 0.05);
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  font-size: 0.75em;
  color: #a0a0b8;
  font-family: var(--font-mono);
}
.md-content .code-lang {
  text-transform: lowercase;
  letter-spacing: 0.02em;
}
.md-content .code-copy-btn {
  background: transparent;
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: #c0c0d0;
  padding: 0.15em 0.6em;
  border-radius: 4px;
  font-size: 0.85em;
  cursor: pointer;
  font-family: inherit;
  transition:
    background 0.15s,
    border-color 0.15s;
}
.md-content .code-copy-btn:hover {
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(255, 255, 255, 0.25);
}
.md-content .code-copy-btn.copied {
  background: rgba(74, 222, 128, 0.15);
  border-color: rgba(74, 222, 128, 0.4);
  color: #4ade80;
}
.md-content .code-block pre.hljs {
  background: transparent;
  color: #e0def4;
  padding: 0.8em 1em;
  overflow-x: auto;
  margin: 0;
  font-size: 0.85em;
  border-radius: 0;
}
.md-content .code-block pre.hljs code {
  background: none;
  padding: 0;
  border-radius: 0;
  color: inherit;
}
/* Legacy fallback for inline pre.hljs without wrapper */
.md-content > pre.hljs {
  background: #1a1a2e;
  color: #e0def4;
  padding: 0.8em 1em;
  border-radius: 8px;
  overflow-x: auto;
  margin: 0.5em 0;
  font-size: 0.85em;
}
html.dark .md-content > pre.hljs {
  background: #0d0d1a;
}
.md-content blockquote {
  border-left: 3px solid #a57eae;
  padding-left: 0.8em;
  margin: 0.5em 0;
  color: var(--color-text-muted);
}
.md-content strong {
  font-weight: 600;
}
.md-content a {
  color: #5a4fcf;
  text-decoration: none;
}
.md-content a:hover {
  text-decoration: underline;
}
html.dark .md-content a {
  color: #8b7bb5;
}
.md-content table {
  border-collapse: collapse;
  margin: 0.5em 0;
  font-size: 0.9em;
}
.md-content th,
.md-content td {
  border: 1px solid var(--color-border);
  padding: 0.3em 0.6em;
}
.md-content th {
  background: rgba(0, 0, 0, 0.04);
  font-weight: 600;
}
html.dark .md-content th {
  background: rgba(255, 255, 255, 0.04);
}
.md-content .katex-display {
  margin: 0.5em 0;
  overflow-x: auto;
}
.md-content .katex {
  font-size: 1.05em;
}
</style>
