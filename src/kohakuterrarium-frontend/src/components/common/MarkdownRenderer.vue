<template>
  <div ref="rootEl" class="md-content" v-html="rendered" @click="onClick" />
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue"
import MarkdownIt from "markdown-it"
import mk from "markdown-it-katex"
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
    return (
      `<div class="code-block">` +
      `<div class="code-header">` +
      `<span class="code-lang">${displayLang}</span>` +
      `<button class="code-copy-btn" data-copy="${md.utils.escapeHtml(str).replace(/"/g, "&quot;")}" title="Copy">Copy</button>` +
      `</div>` +
      `<pre class="hljs"><code>${highlighted}</code></pre>` +
      `</div>`
    )
  },
})

md.use(mk)

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

watch(
  () => props.content,
  async () => {
    await nextTick()
  },
)

/**
 * Pre-process content to normalize LaTeX delimiters:
 * - \( ... \) -> $ ... $  (inline)
 * - \[ ... \] -> $$ ... $$ (block, on own lines)
 * - Ensure $$ blocks have blank lines around them
 */
function preprocessLatex(text) {
  if (!text) return ""

  // Convert \( ... \) to $ ... $ for inline math
  text = text.replace(/\\\((.+?)\\\)/g, (_, math) => `$${math}$`)

  // Convert \[ ... \] to $$ ... $$ for block math (may span lines)
  text = text.replace(/\\\[([\s\S]*?)\\\]/g, (_, math) => `\n$$${math.trim()}$$\n`)

  // Ensure $$ blocks are surrounded by blank lines for proper parsing
  text = text.replace(/([^\n])(\n\$\$)/g, "$1\n$2")
  text = text.replace(/(\$\$\n)([^\n])/g, "$1\n$2")

  return text
}

const rendered = computed(() => {
  if (!props.content) return ""
  return md.render(preprocessLatex(props.content))
})
</script>

<style>
@import "katex/dist/katex.min.css";

.md-content {
  line-height: 1.65;
  word-wrap: break-word;
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
