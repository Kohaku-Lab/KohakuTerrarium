/**
 * Command palette store. Holds a registry of commands plus open
 * state and the active query. Commands are registered from anywhere
 * (layout store, panels, composables) via `register()`.
 *
 * A command entry:
 *   {
 *     id: string,           // unique id
 *     label: string,        // visible title
 *     icon?: string,        // uno icon class, optional
 *     prefix?: string,      // '>', '@', '#', '/'; default '>'
 *     keywords?: string,    // extra searchable tokens
 *     handler: () => any,   // fired on commit
 *     shortcut?: string,    // shown in the row
 *   }
 */

import { defineStore } from "pinia"
import { computed, ref } from "vue"

function _score(entry, query) {
  if (!query) return 1
  const q = query.toLowerCase()
  const hay = `${entry.label} ${entry.id} ${entry.keywords || ""}`.toLowerCase()
  // Exact substring gets the best score.
  if (hay.includes(q)) return 10 - Math.max(0, hay.indexOf(q) / 20)
  // Character-subsequence fallback: every query char appears in order.
  let i = 0
  for (const ch of q) {
    i = hay.indexOf(ch, i)
    if (i === -1) return 0
    i++
  }
  return 1
}

export const usePaletteStore = defineStore("palette", () => {
  const commands = ref(/** @type {Array<object>} */ ([]))
  const open = ref(false)
  const query = ref("")

  function register(cmd) {
    if (!cmd || !cmd.id || !cmd.handler) return
    const existing = commands.value.findIndex((c) => c.id === cmd.id)
    if (existing >= 0) {
      commands.value[existing] = { prefix: ">", ...cmd }
    } else {
      commands.value = [...commands.value, { prefix: ">", ...cmd }]
    }
  }

  function unregister(id) {
    commands.value = commands.value.filter((c) => c.id !== id)
  }

  function openPalette(initialQuery = "") {
    query.value = initialQuery
    open.value = true
  }

  function closePalette() {
    open.value = false
    query.value = ""
  }

  /** Parse the query into `{prefix, text}`. Leading `>`/`@`/`#`/`/` wins. */
  const parsed = computed(() => {
    const raw = query.value.trim()
    if (!raw) return { prefix: ">", text: "" }
    const c = raw[0]
    if (c === ">" || c === "@" || c === "#" || c === "/") {
      return { prefix: c, text: raw.slice(1).trim() }
    }
    return { prefix: ">", text: raw }
  })

  const results = computed(() => {
    const { prefix, text } = parsed.value
    const filtered = commands.value.filter((c) => (c.prefix || ">") === prefix)
    const scored = filtered
      .map((c) => ({ ...c, score: _score(c, text) }))
      .filter((c) => c.score > 0)
    scored.sort((a, b) => b.score - a.score)
    return scored.slice(0, 50)
  })

  function run(id) {
    const cmd = commands.value.find((c) => c.id === id)
    if (!cmd) return
    try {
      cmd.handler()
    } catch (err) {
      console.error("Palette command failed", id, err)
    }
    closePalette()
  }

  return {
    commands,
    open,
    query,
    parsed,
    results,
    register,
    unregister,
    openPalette,
    closePalette,
    run,
  }
})
