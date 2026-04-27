/**
 * Canvas store — artifact list derived from the chat
 * stream. Frontend-only: no backend endpoint.
 *
 * Phase 7 scope: detect long code / markdown / html chunks in
 * assistant messages or explicit `##canvas##` / `##artifact##`
 * markers, index them by source message id, and expose them to the
 * Canvas panel. Regeneration of the same source block appends a
 * version rather than a new artifact.
 */

import { defineStore } from "pinia"
import { computed, ref } from "vue"

import { useChatStore } from "@/stores/chat"

function scopeKeyOf(scope) {
  const instanceId = scope?.instanceId || ""
  const sessionId = scope?.sessionId || ""
  const tab = scope?.tab || ""
  return `${instanceId}::${sessionId}::${tab}`
}

const MIN_LINES_FOR_HEURISTIC = 15
// Match fenced code blocks: opening ```lang\n ... closing ```
// Uses \n``` on its own line (not $ anchor which is fragile with \r\n).
const CODE_FENCE = /```(\w*)\n([\s\S]*?)\n```/g
// Simple multi-line extractor for fenced blocks or explicit markers.

/**
 * Best-effort language guess from the opening fence info string, or
 * `text` when no hint is present.
 */
function _langOrText(info) {
  if (!info) return "text"
  const s = String(info).trim().toLowerCase()
  return s || "text"
}

function _guessTypeFromLang(lang) {
  if (!lang) return "code"
  if (lang === "md" || lang === "markdown") return "markdown"
  if (lang === "html" || lang === "htm") return "html"
  if (lang === "svg") return "svg"
  if (lang === "mermaid") return "diagram"
  return "code"
}

/** ``data:image/png;base64,...`` → ``png``; falls back to "" for unknown URLs. */
function _extOfDataUrl(url) {
  if (typeof url !== "string") return ""
  const m = /^data:image\/([\w+.-]+);/i.exec(url)
  return m ? m[1].toLowerCase() : ""
}

function _artifactName(seed) {
  const trimmed = (seed || "").trim().split("\n")[0] || "artifact"
  return trimmed.length > 60 ? trimmed.slice(0, 60) + "…" : trimmed
}

export const useCanvasStore = defineStore("canvas", () => {
  const artifactsByScope = ref({})
  const activeIdByScope = ref({})
  const dismissedByScope = ref({})
  const currentScope = ref({ instanceId: "", sessionId: "", tab: "" })

  const currentScopeKey = computed(() => scopeKeyOf(currentScope.value))
  const artifacts = computed(() => artifactsByScope.value[currentScopeKey.value] || [])
  const activeId = computed(() => activeIdByScope.value[currentScopeKey.value] || null)
  const dismissed = computed(() => !!dismissedByScope.value[currentScopeKey.value])

  function setScope(scope = {}) {
    currentScope.value = {
      instanceId: scope.instanceId || "",
      sessionId: scope.sessionId || "",
      tab: scope.tab || "",
    }
  }

  function _setArtifacts(scopeKey, items) {
    artifactsByScope.value = { ...artifactsByScope.value, [scopeKey]: items }
  }

  function _setActiveId(scopeKey, value) {
    activeIdByScope.value = { ...activeIdByScope.value, [scopeKey]: value }
  }

  function _setDismissed(scopeKey, value) {
    dismissedByScope.value = { ...dismissedByScope.value, [scopeKey]: value }
  }

  /** Upsert an artifact. Skips if sourceId exists with same content. */
  function upsertArtifact({ sourceId, content, lang, type, seedName, scope = currentScope.value }) {
    const scopeKey = scopeKeyOf(scope)
    const list = artifactsByScope.value[scopeKey] || []
    const existing = list.find((a) => a.sourceId === sourceId)
    if (existing) {
      if (existing.content === content) return existing
      existing.content = content
      existing.lang = lang || existing.lang
      existing.type = type || existing.type
      _setArtifacts(scopeKey, [...list])
      if (!activeIdByScope.value[scopeKey]) _setActiveId(scopeKey, existing.id)
      return existing
    }
    const id = `artifact_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`
    const a = {
      id,
      sourceId,
      name: _artifactName(seedName || content),
      type: type || _guessTypeFromLang(lang),
      content,
      lang: lang || "text",
      scopeKey,
    }
    _setArtifacts(scopeKey, [...list, a])
    if (!activeIdByScope.value[scopeKey]) _setActiveId(scopeKey, id)
    return a
  }

  /** Scan a single assistant message for fenced blocks or markers.
   *  Assistant messages use `parts: [{type, content}]`, not `.content`. */
  function scanMessage(msg, scope = currentScope.value) {
    if (!msg || msg.role !== "assistant") return
    // Image parts (provider-native ``image_gen`` outputs, etc.) become
    // image artifacts so they show up in the Canvas alongside long
    // code blocks. The url can be a data: URL (Codex inlines them
    // base64) or a session-relative path the backend rewrote.
    if (msg.parts && Array.isArray(msg.parts)) {
      let imgIdx = 0
      for (const p of msg.parts) {
        if (p.type !== "image_url") continue
        const url = p.image_url?.url
        if (!url) continue
        const meta = p.meta || {}
        const lang = (meta.output_format || _extOfDataUrl(url) || "png").toLowerCase()
        upsertArtifact({
          scope,
          sourceId: `${scopeKeyOf(scope)}:${msg.id}:image:${imgIdx}`,
          content: url,
          lang,
          type: "image",
          seedName:
            meta.revised_prompt || meta.source_name || meta.source_type || `image_${imgIdx + 1}`,
        })
        imgIdx += 1
      }
    }
    // Assemble full text from parts (the chat store's message format).
    let text = ""
    if (msg.parts && Array.isArray(msg.parts)) {
      for (const p of msg.parts) {
        if (p.type === "text" && p.content) text += p.content
      }
    } else if (msg.content) {
      text = String(msg.content)
    }
    if (!text) return

    // Explicit `##canvas##` / `##artifact##` markers take precedence.
    // Syntax: `##canvas name=foo lang=py##...##canvas##`
    const markerRe = /##(?:canvas|artifact)(?:\s+([^#]*))?##\n?([\s\S]*?)##(?:canvas|artifact)##/g
    let m
    while ((m = markerRe.exec(text)) !== null) {
      const meta = (m[1] || "").trim()
      const body = m[2] || ""
      const lang = /lang=([\w-]+)/.exec(meta)?.[1] || "text"
      const name = /name=([^\s]+)/.exec(meta)?.[1] || null
      upsertArtifact({
        scope,
        sourceId: `${scopeKeyOf(scope)}:${msg.id}:marker:${m.index}`,
        content: body,
        lang,
        type: _guessTypeFromLang(lang),
        seedName: name,
      })
    }

    // Fallback: long fenced code blocks become artifacts.
    CODE_FENCE.lastIndex = 0
    let f
    while ((f = CODE_FENCE.exec(text)) !== null) {
      const lang = _langOrText(f[1])
      const body = f[2] || ""
      const lines = body.split("\n").length
      if (lines < MIN_LINES_FOR_HEURISTIC) continue
      upsertArtifact({
        scope,
        sourceId: `${scopeKeyOf(scope)}:${msg.id}:fence:${f.index}`,
        content: body,
        lang,
        type: _guessTypeFromLang(lang),
      })
    }
  }

  /** Drain the chat store's current tab messages and update artifacts. */
  function syncFromChatStore() {
    const chat = useChatStore()
    const tab = chat.activeTab
    if (!tab) return
    const scope = {
      instanceId: chat._instanceId || "",
      sessionId: chat.sessionInfo.sessionId || "",
      tab,
    }
    setScope(scope)
    const msgs = chat.messagesByTab?.[tab] || []
    for (const m of msgs) {
      if (m.role !== "assistant") continue
      scanMessage(m, scope)
    }
  }

  function setActive(id) {
    const scopeKey = currentScopeKey.value
    if ((artifactsByScope.value[scopeKey] || []).some((a) => a.id === id)) {
      _setActiveId(scopeKey, id)
    }
  }

  function dismiss() {
    _setDismissed(currentScopeKey.value, true)
  }

  function reset(scope = currentScope.value) {
    const scopeKey = scopeKeyOf(scope)
    const nextArtifacts = { ...artifactsByScope.value }
    const nextActive = { ...activeIdByScope.value }
    const nextDismissed = { ...dismissedByScope.value }
    delete nextArtifacts[scopeKey]
    delete nextActive[scopeKey]
    delete nextDismissed[scopeKey]
    artifactsByScope.value = nextArtifacts
    activeIdByScope.value = nextActive
    dismissedByScope.value = nextDismissed
  }

  const activeArtifact = computed(
    () => artifacts.value.find((a) => a.id === activeId.value) || null,
  )

  // activeVersion kept as alias for backward compat — just returns the artifact itself.
  const activeVersion = computed(() => activeArtifact.value)

  return {
    artifacts,
    activeId,
    activeArtifact,
    activeVersion,
    dismissed,
    currentScope,
    artifactsByScope,
    activeIdByScope,
    dismissedByScope,
    setScope,
    upsertArtifact,
    scanMessage,
    syncFromChatStore,
    setActive,
    dismiss,
    reset,
  }
})
