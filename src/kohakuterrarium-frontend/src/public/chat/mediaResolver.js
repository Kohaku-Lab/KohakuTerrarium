// Host-neutral media resolution seam shared by the Dashboard and the VS Code
// webview. A host installs one resolver via ``provideMediaResolver``; shared
// components consume it through ``useMediaResolver`` without importing any
// host transport. The Dashboard keeps the direct same-origin browser resolver
// (no round trip, native ``<a download>``); the VS Code webview wraps the Host's
// ``media.prepare``/open/save surface in the same interface.
import { inject, onBeforeUnmount, provide, ref, watch } from "vue"

// ``export ... from`` does not create a local binding, so the same names must be
// imported before they can be re-exported AND used by the browser resolver below.
import { fileReferencePath, mediaSourceUrl, safeArtifactUrl, safeMediaParts } from "./mediaRefs.js"

export { fileReferencePath, mediaSourceUrl, safeArtifactUrl, safeMediaParts }

export const MEDIA_RESOLVER_KEY = "ktMediaResolver"

/** The pass-through rules the shared renderer applies to inline image
 * references: data/blob (and other relative) paths stay as-is, http(s) is
 * allowed, ``file://`` references map to the same-origin raw file route, and a
 * scheme-bearing non-http value is dropped. */
export function safeImageUrl(value) {
  if (typeof value !== "string") return ""
  if (value.startsWith("data:image/") || value.startsWith("blob:")) return value
  if (value.startsWith("/") && !value.startsWith("//")) return value
  if (!/^[a-z][a-z\d+.-]*:/i.test(value) && !value.startsWith("//")) return value
  const raw = mediaSourceUrl(value)
  if (raw) return raw
  try {
    const url = new URL(value)
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : ""
  } catch {
    return ""
  }
}

/**
 * The Dashboard (browser) resolver: a synchronous direct same-origin URL for
 * inline media, no Host round trip and no per-resource lease. Open/save are the
 * native browser behaviors (``<a download>``), so ``canOpen``/``canSave`` stay
 * false and the shared components fall back to the plain anchor.
 */
export function createBrowserMediaResolver() {
  return {
    kind: "browser",
    resolveImage(value) {
      return safeImageUrl(value)
    },
    resolveMedia(value) {
      // ``mediaSourceUrl`` maps session artifacts and local ``file://``
      // references to their same-origin routes; anything else yields "".
      return mediaSourceUrl(value)
    },
    release() {},
    canOpen: false,
    canSave: false,
  }
}

export function provideMediaResolver(resolver) {
  provide(MEDIA_RESOLVER_KEY, resolver)
}

export function useMediaResolver() {
  return inject(MEDIA_RESOLVER_KEY, null) || createBrowserMediaResolver()
}

/**
 * Resolve one media reference into a displayable URL, cancelling any in-flight
 * Host prepare when the reference changes or the consumer unmounts. A resolver
 * may answer synchronously (browser: a plain string) or asynchronously (Host: a
 * thenable of ``{ url, name?, mime?, resourceId? }``); both are handled without
 * forcing the caller to know which.
 *
 * ``signal`` is passed to the resolver so a Host prepare that settles after
 * cancellation can discard its spooled resource. It is NOT just a post-settle
 * flag: ``signal.onCancel`` lets a resolver abort an in-flight read the moment
 * the consumer cancels, instead of waiting for the response to arrive.
 */
export function useMediaResource(source, { kind = "media", name = "" } = {}) {
  const resolver = useMediaResolver()
  const url = ref("")
  const state = ref("idle")
  const error = ref("")
  const readableName = typeof name === "function" ? name : () => name
  let resource = null
  let op = null
  let token = 0
  let stopGenerationWatch = null

  const releaseResource = () => {
    if (resource?.resourceId) resolver.release?.(resource.resourceId)
    resource = null
  }
  const cancel = () => {
    if (op) {
      op.disposed = true
      // Actively tear down the in-flight read (Host: send ``media.cancel`` with
      // the prepare requestId) rather than waiting for it to settle.
      op.onCancel?.()
    }
    op = null
    releaseResource()
  }

  function load(value) {
    const my = ++token
    cancel()
    url.value = ""
    error.value = ""
    if (!value) {
      state.value = "idle"
      return
    }
    const current = { disposed: false, onCancel: null }
    op = current
    let resolved
    try {
      resolved =
        kind === "image"
          ? resolver.resolveImage?.(value, { name: readableName(), signal: current })
          : resolver.resolveMedia?.(value, { name: readableName(), signal: current })
    } catch (cause) {
      state.value = "error"
      error.value = cause?.message || String(cause)
      return
    }
    if (!resolved) {
      state.value = "idle"
      return
    }
    if (typeof resolved.then !== "function") {
      if (my !== token || current.disposed) return
      url.value = resolved || ""
      state.value = url.value ? "ready" : "idle"
      return
    }
    state.value = "loading"
    Promise.resolve(resolved)
      .then((result) => {
        if (my !== token || current.disposed) return
        resource = result && typeof result === "object" ? result : null
        url.value = result?.url || ""
        state.value = url.value ? "ready" : "idle"
      })
      .catch((cause) => {
        if (my !== token || current.disposed) return
        state.value = "error"
        error.value = cause?.message || String(cause)
      })
  }

  watch(source, load, { immediate: true })
  // A Host resolver owns a generation that flips when the ready/selection fence
  // changes. An unchanged reference must re-resolve against the new fence, so it
  // reloads (releasing the previous spool) instead of showing a stale URI.
  if (resolver.generation) stopGenerationWatch = watch(resolver.generation, () => load(source()))
  onBeforeUnmount(() => {
    token++
    // Stop the generation watcher explicitly rather than relying on the implicit
    // component-scope teardown: a fence change racing the unmount must never
    // re-issue a Host prepare whose spooled resource would be orphaned.
    stopGenerationWatch?.()
    stopGenerationWatch = null
    cancel()
    // The released Host lease invalidates the spooled URI; drop it so no dead
    // URI is left readable on the (now unmounted) component.
    url.value = ""
    state.value = "idle"
  })

  return {
    url,
    state,
    error,
    canOpen: !!resolver.canOpen,
    canSave: !!resolver.canSave,
    open: () => (resource?.resourceId ? resolver.open?.(resource.resourceId) : undefined),
    save: () => (resource?.resourceId ? resolver.save?.(resource.resourceId) : undefined),
    reload: () => load(source()),
  }
}

/**
 * Resolve every ``<img>`` inside a markdown host element through the injected
 * resolver. Markdown renders through ``v-html``, so images cannot be Vue
 * components; this is the shared, host-neutral seam that lets the VS Code
 * webview swap artifact refs for Host-spooled URIs while the Dashboard's browser
 * resolver keeps the direct same-origin URL (a no-op). It is a post-render pass
 * on the shared component, not an Extension-only DOM MutationObserver.
 *
 * The raw reference is kept on ``data-media-src`` so a later pass recognizes an
 * unchanged image after ``v-html`` recreates its node. Each pass DIFFS against
 * the previous one instead of disposing every lease: streaming re-renders append
 * text, so the same artifact shows up on every frame — disposing and
 * re-preparing it there would starve the Host with a full refetch per frame. A
 * source still present in the new render keeps its lease, rebinds to the
 * recreated node(s), and replays the already-resolved URL (a still-pending read
 * applies once it settles); only a source that no longer appears is dropped and
 * released exactly once. The resolver's ready/selection ``generation`` is part
 * of the identity: when it flips, even an unchanged reference is invalidated so
 * no stale spooled URI survives the fence change.
 *
 * A FAILED read is NOT silent: the raw ``<img>`` is replaced by the same
 * production ``.kt-media-status.is-error`` leaf the shared ``MediaImage``/
 * ``VideoFilePreview`` components render, carrying the localized
 * ``chat.media.unavailable`` string the injected resolver's dictionary supplies
 * (never the raw Host rejection, which could leak a token). The failed op is
 * KEPT in the diff map, so a streaming re-render of the same source shows the
 * status without firing a fresh Host read per frame (no busy retry loop); an
 * explicit ``retry(src)`` or a fence flip drops it and re-prepares exactly once.
 *
 * A settled Host lease clears ``signal.onCancel``, so its ``resourceId`` handle
 * is tracked and released exactly once on drop/replacement; a still-pending
 * lease is actively aborted through ``onCancel``, and a read that settles after
 * its lease was dropped releases the just-arrived handle rather than orphaning
 * the spooled resource.
 *
 * ``dispose`` is the full teardown (unmount / cleared content): it releases every
 * lease AND stops the generation watcher, so a fence change on a detached tree
 * cannot start a fresh Host read. A later ``resolve`` re-arms the watcher, so the
 * one instance survives an empty frame mid-lifetime without leaking the watch.
 */
export function createMarkdownMediaResolver(resolver) {
  const ops = new Map() // src -> op: one lease per unique image source
  let lastRoot = null
  let generation = resolver.generation ? resolver.generation.value : undefined
  let stopGeneration = null

  // The visible error text is the shared ``chat.media.*`` dictionary value the
  // resolver was given (the VS Code host bridge injects the real ``t``). The
  // fallback is the English source string, so the Dashboard and any
  // partially-translated locale still read coherently. The raw rejection reason
  // is NEVER rendered — a Host transport error (or a token inside it) must not
  // reach the transcript.
  const unavailableText = () => {
    const translate = resolver.translate
    if (typeof translate === "function") {
      const text = translate("chat.media.unavailable")
      if (text && text !== "chat.media.unavailable") return text
    }
    return "Media unavailable"
  }

  const isStatusNode = (node) => node?.nodeType === 1 && node.classList?.contains("kt-media-status")

  // The same production status leaf ``MediaImage``/``VideoFilePreview`` render:
  // a plain role=status span (visible text, not an invisible ``title``), styled
  // by the shared ``.kt-media-status.is-error`` rule in conversation-message.css.
  // Elements are created from the owning node's own document so this shared graph
  // never reaches for a host global.
  const makeStatus = (doc, src, alt) => {
    const span = doc.createElement("span")
    span.className = "kt-media-status is-error"
    span.setAttribute("role", "status")
    span.setAttribute("data-media-src", src)
    span.setAttribute("data-media-status", "error")
    if (alt) span.setAttribute("data-media-alt", alt)
    span.textContent = unavailableText()
    return span
  }

  const makeImage = (doc, src, alt, url) => {
    const img = doc.createElement("img")
    img.setAttribute("src", url)
    img.setAttribute("data-media-src", src)
    if (alt) img.setAttribute("alt", alt)
    return img
  }

  // Render one source's current state onto every node sharing it. A resolved URL
  // keeps the ``<img>`` (or restores one over a stale status), a failure REPLACES
  // the ``<img>`` with the shared status leaf (preserving the raw source + alt so
  // a retry/removal still recognizes it), and a pending read leaves the node
  // without a ``src`` (never the raw route).
  const renderNode = (node, op) => {
    if (op.error) {
      if (isStatusNode(node)) node.textContent = unavailableText()
      else
        node.replaceWith(makeStatus(node.ownerDocument, op.src, node.getAttribute?.("alt") || ""))
      return
    }
    if (op.resolvedUrl) {
      if (isStatusNode(node)) {
        node.replaceWith(
          makeImage(
            node.ownerDocument,
            op.src,
            node.getAttribute("data-media-alt") || "",
            op.resolvedUrl,
          ),
        )
      } else {
        node.removeAttribute("data-media-status")
        node.setAttribute("src", op.resolvedUrl)
      }
      return
    }
    if (!isStatusNode(node)) node.removeAttribute("src")
  }

  const apply = (op) => {
    for (const node of op.nodes) renderNode(node, op)
  }

  // Drop one tracked lease exactly once: release a settled handle, abort a
  // still-pending read. A pending op is NOT marked released here: the handle it
  // will hand back does not exist yet, and marking it released would suppress
  // the release that must still fire when the response finally arrives. The
  // dropped flag makes a duplicate dispose a no-op and lets a late settle
  // release exactly once.
  const drop = (op) => {
    if (op.dropped) return
    op.dropped = true
    op.disposed = true
    if (op.resourceId) {
      op.released = true
      resolver.release?.(op.resourceId)
    } else op.onCancel?.()
  }

  // Drop every lease without touching the generation watcher. Used both by the
  // fence-change path (which stays armed) and by ``dispose`` below.
  const disposeLeases = () => {
    for (const op of ops.values()) drop(op)
    ops.clear()
  }

  const dispose = () => {
    stopGeneration?.()
    stopGeneration = null
    disposeLeases()
  }

  // Group the current render's images by raw reference. A re-render recreates
  // the nodes, so the same source can map to several nodes (duplicate copies).
  // A failed source is represented by its status leaf, so it must be collected
  // too — otherwise a fence-flip pass over an un-recreated tree would treat the
  // failure as "removed" and lose the op it must keep.
  const collect = (root) => {
    const next = new Map()
    for (const node of root.querySelectorAll("img, .kt-media-status[data-media-src]")) {
      const src = node.dataset?.mediaSrc || node.getAttribute("src")
      if (!src) continue
      if (node.dataset) node.dataset.mediaSrc = src
      const nodes = next.get(src)
      if (nodes) nodes.push(node)
      else next.set(src, [node])
    }
    return next
  }

  // Mark a read as failed and keep its op in the map: the visible status is
  // replayed on every later pass over the same source, but NO further Host read
  // is issued until an explicit ``retry``/fence flip drops this op.
  const fail = (op) => {
    if (op.dropped || op.disposed || op.released) return
    op.error = true
    op.resolvedUrl = ""
    if (!ops.has(op.src)) ops.set(op.src, op)
    apply(op)
  }

  const createOp = (src, nodes) => {
    const op = {
      src,
      nodes: new Set(nodes),
      disposed: false,
      dropped: false,
      released: false,
      resolvedUrl: "",
      error: false,
      onCancel: null,
      resourceId: "",
    }
    ops.set(src, op)
    let resolved
    try {
      resolved = resolver.resolveImage?.(src, {
        name: nodes[0]?.getAttribute("alt") || "",
        signal: op,
      })
    } catch {
      fail(op)
      return
    }
    if (!resolved) {
      // Not a displayable reference (unsupported scheme): the shared component
      // stays idle rather than showing a false error.
      apply(op)
      ops.delete(src)
      return
    }
    if (typeof resolved.then !== "function") {
      ops.delete(src)
      op.resolvedUrl = resolved
      if (resolved !== src) apply(op)
      return
    }
    apply(op)
    Promise.resolve(resolved)
      .then((result) => {
        const url = result?.url || ""
        const resourceId = result?.resourceId || ""
        // The lease was dropped while the read was in flight. Its lease, if the
        // response carries one, must still be released — never orphaned — but
        // never swapped into a stale image.
        if (op.dropped || op.disposed || op.released) {
          if (resourceId && !op.released) {
            op.released = true
            resolver.release?.(resourceId)
          }
          return
        }
        if (resourceId) op.resourceId = resourceId
        op.resolvedUrl = url
        apply(op)
      })
      .catch(() => {
        fail(op)
      })
  }

  // A ready/selection fence change re-runs the last pass so an unchanged
  // reference re-resolves against the new generation even without a re-render.
  // ``stopGeneration`` is the explicit teardown handle; ``dispose`` clears it and
  // ``resolve`` re-arms it, so the watcher never outlives a disposed instance.
  const armGeneration = () => {
    if (!resolver.generation || stopGeneration) return
    stopGeneration = watch(resolver.generation, () => resolve(lastRoot))
  }

  const resolve = (root) => {
    lastRoot = root || null
    if (root) armGeneration()
    const nextGeneration = resolver.generation ? resolver.generation.value : undefined
    if (nextGeneration !== generation) {
      // The ready/selection fence changed: every lease — including an unchanged
      // reference — is stale, so the whole set is dropped and rebuilt. The
      // watcher stays armed for the next fence change. A failed source is
      // re-prepared here, which is the fence-flip retry.
      disposeLeases()
      generation = nextGeneration
      if (!root) return
      for (const [src, nodes] of collect(root)) createOp(src, nodes)
      return
    }
    if (!root) {
      dispose()
      return
    }
    const next = collect(root)
    for (const [src, nodes] of next) {
      const op = ops.get(src)
      if (op) {
        // Rebind the surviving lease to the recreated node(s) and replay the
        // resolved URL (or the error status); a still-pending read updates these
        // nodes on settle.
        op.nodes = new Set(nodes)
        apply(op)
      } else {
        createOp(src, nodes)
      }
    }
    // Only a reference that no longer appears anywhere is dropped/released.
    for (const [src, op] of [...ops]) {
      if (!next.has(src)) {
        drop(op)
        ops.delete(src)
      }
    }
  }

  // Explicit retry for a failed source (or every failed source when omitted):
  // drop its kept op and re-run the last pass, issuing exactly ONE fresh Host
  // read. This is the honest hook a real handler can call; the shared markdown
  // surface renders no fake button because it owns no per-image handler.
  const retry = (src) => {
    const targets = src == null ? [...ops.values()].filter((op) => op.error) : [ops.get(src)]
    for (const op of targets) {
      if (!op || !op.error) continue
      drop(op)
      ops.delete(op.src)
    }
    resolve(lastRoot)
  }

  armGeneration()

  return { resolve, dispose, retry }
}
