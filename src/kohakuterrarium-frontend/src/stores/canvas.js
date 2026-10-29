/**
 * Canvas store — artifact list derived from the chat stream.
 * Frontend-only: no backend endpoint.
 *
 * The canvas holds generated images, canvas-surface widgets (``show_app``,
 * page-mode ``show_ui``) and what the user pins with "Open in canvas".
 * Sources: provider-native image outputs, ``canvas_image`` previews, widget
 * messages whose surface is ``canvas``, user pins, and (read-only, for one
 * release) ``##canvas##`` / ``##artifact##`` markers. Message-derived
 * artifacts are keyed by source-message id. Within one scan, a new key whose
 * content matches an artifact (or a closed block) whose own key is gone from
 * the scan takes that artifact (or its closed state) over — message ids
 * change when history reloads — and a new key repeating a block another
 * scanned message still shows adds nothing. Image previews are keyed by file
 * path, so re-touching a file refreshes it in place.
 *
 * **Per-scope** (scope = attach target). Two attach tabs each have
 * their own artifact list, active selection, and dismissed flag — no
 * race over a shared "currentScope" pointer. Outside a provider, the
 * default scope keeps the v1 singleton behaviour.
 */

import { defineStore } from "pinia"
import { computed, getCurrentInstance, ref } from "vue"

import { injectScope, disposeScopeStore, registerScopeDisposer } from "@/composables/useScope"
import { mediaSourceUrl } from "@/utils/artifacts"
import { readLocalJsonPref, writeLocalJsonPref } from "@/utils/uiPrefs"

/** Soft cap on the strip. Oldest tiles are hidden (and stay hidden on
 *  rescan) so a long attach does not grow an unbounded tab list. */
export const MAX_CANVAS_ARTIFACTS = 12

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

function _revisionFingerprint(revision) {
  let first = 0x811c9dc5
  let second = 0x9e3779b9
  for (let i = 0; i < revision.length; i++) {
    const code = revision.charCodeAt(i)
    first = Math.imul(first ^ code, 0x01000193)
    second = Math.imul(second ^ code, 0x85ebca6b)
  }
  return `${revision.length}:${(first >>> 0).toString(16)}:${(second >>> 0).toString(16)}`
}

function _readDismissals(key) {
  try {
    const saved = readLocalJsonPref(key, [])
    if (!Array.isArray(saved)) return new Map()
    return new Map(
      saved
        .filter(
          (entry) =>
            Array.isArray(entry) && typeof entry[0] === "string" && Array.isArray(entry[1]),
        )
        .map(([source, revisions]) => [
          source,
          new Set(revisions.filter((value) => typeof value === "string")),
        ]),
    )
  } catch {
    return new Map()
  }
}

function _toolPreview(part) {
  if (part.resultMeta?.canvas_preview) return part.resultMeta.canvas_preview
  if (part.name !== "canvas_image" || part.status !== "done") return null
  const parts = part.resultParts || (Array.isArray(part.result) ? part.result : [])
  const image = parts.find(
    (item) => item?.type === "image_url" && typeof item.image_url?.url === "string",
  )
  if (!image) return null
  const description = parts.find((item) => item?.type === "text")?.text || ""
  const summary = /^Canvas: ([\s\S]+) \(\d+KB, (png|jpg|jpeg|webp|gif)\)$/.exec(description)
  const path = part.args?.path || summary?.[1] || image.meta?.source_name
  if (typeof path !== "string" || !path) return null
  return {
    kind: "image",
    file_path: path,
    content: image.image_url.url,
    lang: summary?.[2] || image.meta?.output_format || _extOfDataUrl(image.image_url.url) || "png",
  }
}

function _setupCanvasStore(scope) {
  return () => {
    const storageKey = `kt-canvas-dismissals:${scope || "default"}`
    const dismissedRevisions = _readDismissals(storageKey)
    const artifacts = ref([])
    const activeId = ref(null)
    const dismissed = ref(false)
    const hiddenSourceIds = ref(new Set(dismissedRevisions.keys()))
    const seenContentBySource = ref(new Map())
    const pinnedWidgets = ref(new Set())

    const activeArtifact = computed(
      () => artifacts.value.find((a) => a.id === activeId.value) || null,
    )
    // Back-compat alias kept for any caller that imported ``activeVersion``.
    const activeVersion = activeArtifact

    function persistDismissals() {
      try {
        writeLocalJsonPref(
          storageKey,
          dismissedRevisions.size
            ? [...dismissedRevisions].map(([source, revisions]) => [source, [...revisions]])
            : null,
        )
      } catch {
        // Dismissals remain active in memory when browser storage is unavailable.
      }
    }

    function _hideSource(sourceId) {
      const next = new Set(hiddenSourceIds.value)
      next.add(sourceId)
      hiddenSourceIds.value = next
      const revisions = new Set(dismissedRevisions.get(sourceId))
      for (const revision of seenContentBySource.value.get(sourceId) || []) {
        revisions.add(_revisionFingerprint(revision))
      }
      dismissedRevisions.set(sourceId, revisions)
      persistDismissals()
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
      if (revisionId != null && dismissedRevisions.size) {
        const fingerprint = _revisionFingerprint(revision)
        if ([...dismissedRevisions.values()].some((revisions) => revisions.has(fingerprint))) {
          _noteSeen(sourceId, revision)
          return null
        }
      }
      if (hiddenSourceIds.value.has(sourceId)) {
        const seen = seenContentBySource.value.get(sourceId)
        if (
          seen?.has(revision) ||
          dismissedRevisions.get(sourceId)?.has(_revisionFingerprint(revision))
        ) {
          _noteSeen(sourceId, revision)
          return null
        }
        const next = new Set(hiddenSourceIds.value)
        next.delete(sourceId)
        hiddenSourceIds.value = next
        dismissedRevisions.delete(sourceId)
        persistDismissals()
      }
      _noteSeen(sourceId, revision)
      const existing = artifacts.value.find(
        (a) => a.sourceId === sourceId || (revisionId != null && a.revisionId === revisionId),
      )
      if (existing) {
        existing.sourceId = sourceId
        existing.revisionId = revisionId
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
        revisionId,
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

    /** Scan one message: a canvas-surface widget, or an assistant
     *  message's image parts, image previews and ``##canvas##`` markers;
     *  one artifact per match. Idempotent — running twice on the same
     *  message produces the same set of artifacts. */
    function scanMessage(msg, upsert = upsertArtifact) {
      if (msg?.role === "widget" && msg.eventId && widgetOnCanvas(msg)) {
        upsert({
          sourceId: `widget:${msg.eventId}`,
          content: msg.eventId,
          lang: "widget",
          type: "widget",
          seedName: msg.title || msg.renderer || "widget",
        })
        return
      }
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

      // Image previews (``canvas_image``) become artifacts keyed by file path,
      // so re-publishing a path refreshes it in place. Text file previews
      // from write / edit / multi_edit stay in their tool block.
      if (msg.parts && Array.isArray(msg.parts)) {
        for (const [partIndex, p] of msg.parts.entries()) {
          if (p.type !== "tool") continue
          const preview = _toolPreview(p)
          if (!preview || preview.kind !== "image" || preview.content == null) continue
          if (!preview.file_path) continue
          const raw = preview.content
          const revisionId = p.jobId || p.id || `${msg.id}:tool:${partIndex}`
          let content = mediaSourceUrl(raw) || raw
          if (raw.startsWith("file://") && content.startsWith("/api/files/raw?")) {
            content += `&canvas_revision=${encodeURIComponent(revisionId)}`
          }
          upsert({
            sourceId: `file:${preview.file_path}`,
            revisionId,
            content,
            lang: preview.lang || "png",
            type: "image",
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

      // ``##canvas##`` / ``##artifact##`` markers, kept read-only for one
      // release. Syntax: ``##canvas name=foo lang=py##...##canvas##``
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
    }

    /** Move a closed source's hidden state (and its persisted dismissal) to `sourceId`. */
    function _transferHidden(fromId, sourceId) {
      const hidden = new Set(hiddenSourceIds.value)
      hidden.delete(fromId)
      hidden.add(sourceId)
      hiddenSourceIds.value = hidden
      const seen = new Map(seenContentBySource.value)
      seen.set(sourceId, seen.get(fromId) || new Set())
      seen.delete(fromId)
      seenContentBySource.value = seen
      dismissedRevisions.set(sourceId, dismissedRevisions.get(fromId) || new Set())
      dismissedRevisions.delete(fromId)
      persistDismissals()
    }

    /**
     * Reconcile a message-derived block whose sourceId is new to the
     * store against the scan's live keys: true when it was absorbed (a
     * live twin shows it, an orphaned artifact was re-keyed to it, or an
     * orphaned closed block's dismissal moved to it).
     */
    function _absorbRekeyed({ sourceId, content }, live) {
      const revision = JSON.stringify([null, content])
      const twin = artifacts.value.find((a) => a.revisionId == null && a.content === content)
      if (twin) {
        if (!live.has(twin.sourceId)) twin.sourceId = sourceId
        _noteSeen(sourceId, revision)
        return true
      }
      const fingerprint = _revisionFingerprint(revision)
      for (const [closedId, revisions] of dismissedRevisions) {
        if (live.has(closedId) || closedId.startsWith("file:") || !revisions.has(fingerprint))
          continue
        _transferHidden(closedId, sourceId)
        _noteSeen(sourceId, revision)
        return true
      }
      return false
    }

    function scanMessages(messages) {
      const latest = new Map()
      for (const msg of messages) {
        scanMessage(msg, (artifact) => {
          latest.set(artifact.sourceId, artifact)
        })
      }
      for (const artifact of latest.values()) {
        const known =
          artifact.revisionId != null ||
          hiddenSourceIds.value.has(artifact.sourceId) ||
          artifacts.value.some((a) => a.sourceId === artifact.sourceId)
        if (!known && _absorbRekeyed(artifact, latest)) continue
        upsertArtifact(artifact)
      }
    }

    /** Pin a code block the user chose with "Open in canvas". */
    function pinBlock({ content, lang, name } = {}) {
      if (typeof content !== "string" || !content) return null
      const language = (lang || "text").toLowerCase()
      const pinned = upsertArtifact({
        sourceId: `pin:${_revisionFingerprint(content)}`,
        content,
        lang: language,
        type: _guessTypeFromLang(language),
        seedName: name || content,
      })
      if (pinned) activeId.value = pinned.id
      return pinned
    }

    /** Show an inline <ui> block on the canvas (the user chose "Open in canvas"). */
    function pinUi({ source, title } = {}) {
      if (typeof source !== "string" || !source) return null
      const pinned = upsertArtifact({
        sourceId: `ui:${_revisionFingerprint(source)}`,
        content: source,
        lang: "ktui",
        type: "ktui",
        seedName: title || "UI",
      })
      if (pinned) activeId.value = pinned.id
      return pinned
    }

    /** Show a widget message on the canvas (the user moved it there). */
    function pinWidget(message) {
      if (!message?.eventId) return null
      pinnedWidgets.value = new Set([...pinnedWidgets.value, message.eventId])
      const pinned = upsertArtifact({
        sourceId: `widget:${message.eventId}`,
        content: message.eventId,
        lang: "widget",
        type: "widget",
        seedName: message.title || message.renderer || "widget",
      })
      if (pinned) activeId.value = pinned.id
      return pinned
    }

    function widgetOnCanvas(message) {
      return message.surface === "canvas" || pinnedWidgets.value.has(message.eventId)
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
      pinnedWidgets.value = new Set()
      dismissedRevisions.clear()
      persistDismissals()
    }

    return {
      artifacts,
      activeId,
      activeArtifact,
      activeVersion,
      dismissed,
      upsertArtifact,
      pinBlock,
      pinUi,
      pinWidget,
      widgetOnCanvas,
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
    useFn = defineStore(`canvas:${key}`, _setupCanvasStore(key))
    _canvasFactories.set(key, useFn)
    if (scope) {
      registerScopeDisposer(scope, (pinia) => {
        try {
          disposeScopeStore(useFn(pinia), pinia)
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
