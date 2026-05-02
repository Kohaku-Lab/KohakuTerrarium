/**
 * Canvas store — artifact list derived from the chat stream.
 * Frontend-only: no backend endpoint.
 *
 * Detects long code / markdown / html chunks in assistant messages,
 * explicit ``##canvas##`` / ``##artifact##`` markers, and provider-
 * native image outputs; indexes them by source-message id; exposes
 * them to the Canvas panel. Regeneration of the same source block
 * appends a version rather than a new artifact.
 *
 * **Per-scope** (scope = attach target). Two attach tabs each have
 * their own artifact list, active selection, and dismissed flag — no
 * race over a shared "currentScope" pointer. Pre-refactor the store
 * was a singleton with internal ``byScope`` maps; that indirection is
 * gone now (the Pinia scope IS the scope). Outside a provider, the
 * default scope keeps the v1 singleton behaviour.
 */

import { defineStore } from "pinia"
import { computed, getCurrentInstance, ref } from "vue"

import { injectScope, registerScopeDisposer } from "@/composables/useScope"

const MIN_LINES_FOR_HEURISTIC = 15
// Match fenced code blocks: opening ```lang\n ... closing ```
// Uses \n``` on its own line (not $ anchor which is fragile with \r\n).
const CODE_FENCE = /```(\w*)\n([\s\S]*?)\n```/g

/** Best-effort language guess from the opening fence info string, or
 *  ``text`` when no hint is present. */
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

function _setupCanvasStore() {
  return () => {
    const artifacts = ref([])
    const activeId = ref(null)
    const dismissed = ref(false)

    const activeArtifact = computed(
      () => artifacts.value.find((a) => a.id === activeId.value) || null,
    )
    // Back-compat alias kept for any caller that imported ``activeVersion``.
    const activeVersion = activeArtifact

    /** Upsert an artifact. If a sourceId already exists, refresh it in
     *  place; otherwise append. */
    function upsertArtifact({ sourceId, content, lang, type, seedName }) {
      const existing = artifacts.value.find((a) => a.sourceId === sourceId)
      if (existing) {
        if (existing.content === content) return existing
        existing.content = content
        existing.lang = lang || existing.lang
        existing.type = type || existing.type
        existing.name = _artifactName(seedName || content)
        activeId.value = existing.id
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
      }
      artifacts.value = [...artifacts.value, a]
      activeId.value = id
      return a
    }

    /** Scan a single assistant message for image parts, ``##canvas##``
     *  markers, or long fenced code blocks, upserting one artifact per
     *  match. Idempotent — running twice on the same message produces
     *  the same set of artifacts. */
    function scanMessage(msg) {
      if (!msg || msg.role !== "assistant") return

      // Image parts (provider-native ``image_gen`` outputs etc.) become
      // image artifacts. URL can be a data: URL (Codex inlines them) or
      // a session-relative path the backend rewrote.
      if (msg.parts && Array.isArray(msg.parts)) {
        let imgIdx = 0
        for (const p of msg.parts) {
          if (p.type !== "image_url") continue
          const url = p.image_url?.url
          if (!url) continue
          const meta = p.meta || {}
          const lang = (meta.output_format || _extOfDataUrl(url) || "png").toLowerCase()
          upsertArtifact({
            sourceId: `${msg.id}:image:${imgIdx}`,
            content: url,
            lang,
            type: "image",
            seedName:
              meta.revised_prompt || meta.source_name || meta.source_type || `image_${imgIdx + 1}`,
          })
          imgIdx += 1
        }
      }

      // Assemble full text from parts (chat store's message format).
      let text = ""
      if (msg.parts && Array.isArray(msg.parts)) {
        for (const p of msg.parts) {
          if (p.type === "text" && p.content) text += p.content
        }
      } else if (msg.content) {
        text = String(msg.content)
      }
      if (!text) return

      // Explicit ``##canvas##`` / ``##artifact##`` markers take precedence.
      // Syntax: ``##canvas name=foo lang=py##...##canvas##``
      const markerRe = /##(?:canvas|artifact)(?:\s+([^#]*))?##\n?([\s\S]*?)##(?:canvas|artifact)##/g
      let m
      while ((m = markerRe.exec(text)) !== null) {
        const meta = (m[1] || "").trim()
        const body = m[2] || ""
        const lang = /lang=([\w-]+)/.exec(meta)?.[1] || "text"
        const name = /name=([^\s]+)/.exec(meta)?.[1] || null
        upsertArtifact({
          sourceId: `${msg.id}:marker:${m.index}`,
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
          sourceId: `${msg.id}:fence:${f.index}`,
          content: body,
          lang,
          type: _guessTypeFromLang(lang),
        })
      }
    }

    function setActive(id) {
      if (artifacts.value.some((a) => a.id === id)) {
        activeId.value = id
      }
    }

    function dismiss() {
      dismissed.value = true
    }

    function reset() {
      artifacts.value = []
      activeId.value = null
      dismissed.value = false
    }

    return {
      artifacts,
      activeId,
      activeArtifact,
      activeVersion,
      dismissed,
      upsertArtifact,
      scanMessage,
      setActive,
      dismiss,
      reset,
    }
  }
}

const _canvasFactories = new Map()

function _factoryFor(scope) {
  const key = scope || "default"
  let useFn = _canvasFactories.get(key)
  if (!useFn) {
    useFn = defineStore(`canvas:${key}`, _setupCanvasStore())
    _canvasFactories.set(key, useFn)
    if (scope) {
      registerScopeDisposer(scope, () => {
        try {
          useFn().$dispose?.()
        } catch {
          /* swallow */
        }
        _canvasFactories.delete(key)
      })
    }
  }
  return useFn
}

export function useCanvasStore(scope) {
  if (scope !== undefined) return _factoryFor(scope)()
  if (getCurrentInstance()) return _factoryFor(injectScope())()
  return _factoryFor(null)()
}
