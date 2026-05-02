/**
 * Per-attach scope plumbing for the v2 macro shell.
 *
 * **Why this exists.** The v1 frontend assumed *one* running creature
 * per browser tab and shared a single Pinia store across the whole
 * page. v2's macro shell can host multiple ``attach:<creature_id>``
 * tabs simultaneously, so any store carrying per-instance state
 * (chat WS + messages, editor open files, layout preset, status
 * tokens, channel messages) must be **scoped per attach target**
 * rather than shared as a singleton.
 *
 * The pattern: each per-instance store is a Pinia factory that mints
 * one store per scope id (``creature_id`` for solo creatures,
 * ``terrarium_id`` for terrarium attach surfaces). The macro shell
 * tabs that own a target — ``AttachTab`` and the ``Inspector`` tab —
 * call :func:`provideScope` once at the top of their setup so every
 * descendant that calls ``useXStore()`` automatically lands on the
 * scoped store via Vue's ``inject``.
 *
 * **Lifecycle.** Stores live as long as ANY macro shell tab references
 * the scope. The tabs store calls :func:`acquireScope` from
 * ``openTab`` and :func:`releaseScope` from every close path
 * (``closeTab`` / ``closeLeft`` / ``closeRight`` / ``closeOthers`` /
 * ``closeAll``). When the ref count drops to zero the registered
 * disposers fire — closing WS connections and freeing the scoped
 * Pinia stores. Switching between macro tabs does NOT release the
 * scope, so the user can flip back and forth without WS reconnect or
 * history re-fetch (the original "older session keeps reconnecting and
 * flickering" symptom).
 *
 * **v1 back-compat.** Outside an ``AttachTab`` (the v1 page-routed
 * flow at ``/instances/:id`` + the no-instance helper composables)
 * there is no scope provided. ``injectScope()`` returns ``null`` and
 * each store factory falls back to a "default" store — the legacy
 * singleton — so v1 callers see no behavioural change.
 */

import { getCurrentInstance, inject, provide } from "vue"

/** Inject key used by the macro shell to thread the active target
 *  through the descendant component tree. ``string`` (not ``Symbol``)
 *  so it survives across module boundaries when components are split
 *  across chunks. */
export const SCOPE_INJECT_KEY = "kt:scope"

const _refCounts = new Map()
/** scope -> Set<() => void> */
const _disposers = new Map()

/**
 * Provide the active scope to every descendant of the calling
 * component. Call once near the top of the setup of any tab that
 * owns a per-instance target (``AttachTab``, the inspector tab, …).
 *
 *   provideScope(props.tab.target)
 *
 * **Same-component caveat.** Vue 3's ``inject()`` only walks ANCESTOR
 * provides — a component cannot inject its own provide. So if the
 * providing component itself also needs the scoped store, pass the
 * scope explicitly:
 *
 *   provideScope(target.value)
 *   const chat = useChatStore(target.value)   // not useChatStore()
 *
 * Otherwise the providing component lands on the default singleton
 * while every descendant resolves to the scoped store — a silent
 * cross-store split that's hard to spot from the outside.
 */
export function provideScope(scope) {
  provide(SCOPE_INJECT_KEY, scope || null)
}

/**
 * Read the active scope from the surrounding component tree. Returns
 * ``null`` when called outside a setup or when nothing has provided —
 * the latter is the v1 back-compat path.
 */
export function injectScope() {
  if (!getCurrentInstance()) return null
  return inject(SCOPE_INJECT_KEY, null)
}

/** Increase the ref count for a scope. Called by the tabs store when a
 *  macro tab carrying ``target`` is opened. Idempotent for the same
 *  caller — open the same id twice, two refs counted; this matches the
 *  surface model where attach + inspector are independent macro tabs
 *  that may both reference the same creature_id. */
export function acquireScope(scope) {
  if (!scope) return
  _refCounts.set(scope, (_refCounts.get(scope) ?? 0) + 1)
}

/** Decrease the ref count; on transition to zero, fire every
 *  registered disposer for the scope and forget it. Tabs store calls
 *  this when a macro tab carrying ``target`` is closed. */
export function releaseScope(scope) {
  if (!scope) return
  const next = (_refCounts.get(scope) ?? 0) - 1
  if (next > 0) {
    _refCounts.set(scope, next)
    return
  }
  _refCounts.delete(scope)
  const set = _disposers.get(scope)
  if (!set) return
  for (const fn of set) {
    try {
      fn()
    } catch {
      /* swallow — disposer failures must not block other disposers */
    }
  }
  _disposers.delete(scope)
}

/** Register a function to run when the scope's ref count drops to
 *  zero. Each scoped Pinia factory wires one of these so its store +
 *  WS get cleaned up at scope-close time. Call sites are stable (the
 *  factory only registers once per (scope, store) pair). */
export function registerScopeDisposer(scope, fn) {
  if (!scope || typeof fn !== "function") return
  let set = _disposers.get(scope)
  if (!set) {
    set = new Set()
    _disposers.set(scope, set)
  }
  set.add(fn)
}

/**
 * Recover the scope id from a Pinia store's ``$id``.
 *
 * Each scoped store is registered under ``"<store>:<scope>"`` (or
 * ``"<store>:default"`` for the v1 singleton path), so the action
 * can introspect ``this.$id`` to learn its own scope and propagate
 * it to peer stores it needs to call into. Required because actions
 * run detached from any Vue setup context — ``injectScope()``
 * returns null inside an action and would silently fall back to the
 * default singleton, defeating per-attach isolation.
 *
 *     // inside a chat action
 *     const scope = scopeOfStoreId(this.$id)
 *     useStatusStore(scope).reset()    // hits the per-attach store
 */
export function scopeOfStoreId(id) {
  if (typeof id !== "string") return null
  const colon = id.indexOf(":")
  if (colon < 0) return null
  const s = id.slice(colon + 1)
  return s === "default" ? null : s
}

/** Tests only — drop every counter and disposer. Production code
 *  should never need this. */
export function _resetForTests() {
  _refCounts.clear()
  _disposers.clear()
}
