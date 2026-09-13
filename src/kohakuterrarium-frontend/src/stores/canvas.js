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
import { mediaSourceUrl } from "@/utils/artifacts"

const MIN_LINES_FOR_HEURISTIC = 15
/** Soft cap on the strip. Oldest tiles are hidden (and stay hidden on
 *  rescan) so a long attach does not grow an unbounded tab list. */
export const MAX_CANVAS_ARTIFACTS = 12
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
    const hiddenSourceIds = ref(new Set())
    const seenContentBySource = ref(new Map())

    const activeArtifact = computed(
      () => artifacts.value.find((a) => a.id === activeId.value) || null,
    )
    // Back-compat alias kept for any caller that imported ``activeVersion``.
    const activeVersion = activeArtifact

    function _hideSource(sourceId) {
      const next = new Set(hiddenSourceIds.value)
      next.add(sourceId)
      hiddenSourceIds.value = next
    }

    function _noteSeen(sourceId, content) {
      let set = seenContentBySource.value.get(sourceId)
      if (!set) {
        set = new Set()
        const map = new Map(seenContentBySource.value)
        map.set(sourceId, set)
        seenContentBySource.value = map
      }
      set.add(content)
    }

    function _selectNewest() {
      const last = artifacts.value[artifacts.value.length - 1]
      activeId.value = last ? last.id : null
    }

    function _evictOverflow() {
      while (artifacts.value.length > MAX_CANVAS_ARTIFACTS) {
        const oldest = artifacts.value[0]
        _hideSource(oldest.sourceId)
        artifacts.value = artifacts.value.slice(1)
        if (activeId.value === oldest.id) _selectNewest()
      }
    }

    /** Upsert an artifact. Same sourceId refreshes in place without
     *  changing selection. A new sourceId appends and becomes active.
     *  A closed path stays hidden through rescan of known versions, then
     *  reopens for a new publication or updated content. */
    function upsertArtifact({ sourceId, content, lang, type, seedName, revisionId = null }) {
      const revision = JSON.stringify([revisionId, content])
      if (hiddenSourceIds.value.has(sourceId)) {
        const seen = seenContentBySource.value.get(sourceId)
        if (seen && seen.has(revision)) return null
        const next = new Set(hiddenSourceIds.value)
        next.delete(sourceId)
        hiddenSourceIds.value = next
      }
      _noteSeen(sourceId, revision)
      const existing = artifacts.value.find((a) => a.sourceId === sourceId)
      if (existing) {
        if (existing.content === content) return existing
        existing.content = content
        existing.lang = lang || existing.lang
        existing.type = type || existing.type
        existing.name = _artifactName(seedName || content)
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
      _evictOverflow()
      return a
    }

    /** Scan a single assistant message for image parts, file write /
     *  edit tool previews, ``##canvas##`` markers, or long fenced code
     *  blocks, upserting one artifact per match. Idempotent — running
     *  twice on the same message produces the same set of artifacts. */
    function scanMessage(msg, upsert = upsertArtifact) {
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
          upsert({
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

      // Tool parts carrying ``canvas_preview`` become artifacts keyed by
      // file path. write / edit / multi_edit emit text previews; canvas_image
      // emits ``kind: "image"`` whose ``content`` is a displayable URL.
      // Re-touching the same path refreshes the existing artifact in place.
      // ``content === null`` means the file exceeded the preview cap;
      // skip those so the canvas doesn't show an empty bubble.
      if (msg.parts && Array.isArray(msg.parts)) {
        for (const [partIndex, p] of msg.parts.entries()) {
          if (p.type !== "tool") continue
          const preview = p.resultMeta?.canvas_preview
          if (!preview || preview.content == null) continue
          if (!preview.file_path) continue
          const isImage = preview.kind === "image"
          const raw = preview.content
          const revisionId = p.jobId || p.id || `${msg.id}:tool:${partIndex}`
          let content = isImage ? mediaSourceUrl(raw) || raw : raw
          if (isImage && raw.startsWith("file://") && content.startsWith("/api/files/raw?")) {
            content += `&canvas_revision=${encodeURIComponent(revisionId)}`
          }
          upsert({
            sourceId: `file:${preview.file_path}`,
            revisionId,
            content,
            lang: preview.lang || (isImage ? "png" : "text"),
            type: isImage ? "image" : _guessTypeFromLang(preview.lang),
            seedName: preview.file_path,
          })
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
        upsert({
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
        upsert({
          sourceId: `${msg.id}:fence:${f.index}`,
          content: body,
          lang,
          type: _guessTypeFromLang(lang),
        })
      }
    }

    function scanMessages(messages) {
      const latest = new Map()
      for (const msg of messages) {
        scanMessage(msg, (artifact) => {
          latest.set(artifact.sourceId, artifact)
        })
      }
      for (const artifact of latest.values()) {
        upsertArtifact(artifact)
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

    function dismissArtifact(id) {
      const art = artifacts.value.find((a) => a.id === id)
      if (!art) return
      _hideSource(art.sourceId)
      const wasActive = activeId.value === id
      artifacts.value = artifacts.value.filter((a) => a.id !== id)
      if (wasActive) _selectNewest()
    }

    function clearArtifacts() {
      for (const a of artifacts.value) _hideSource(a.sourceId)
      artifacts.value = []
      activeId.value = null
    }

    function reset() {
      artifacts.value = []
      activeId.value = null
      dismissed.value = false
      hiddenSourceIds.value = new Set()
      seenContentBySource.value = new Map()
    }

    return {
      artifacts,
      activeId,
      activeArtifact,
      activeVersion,
      dismissed,
      upsertArtifact,
      scanMessage,
      scanMessages,
      setActive,
      dismiss,
      dismissArtifact,
      clearArtifacts,
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
