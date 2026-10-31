/**
 * Loads package UI extensions for the active host.
 *
 * On start and on every host or user switch it lists the host's UI
 * packages and registers their declared contributions. A package's
 * entry module, styles and message bundles load only when one of its
 * contributions first renders.
 */

import { markRaw, watch } from "vue"

import {
  provideContributionComponent,
  registerPackageContributions,
  unregisterAllContributions,
  openExtensionTab,
} from "@/ext/registry"
import { BUILTIN_UI_PACKAGES } from "@/ext/builtin/index"
import { closeOwnerSocket } from "@/ext/ownerSocket"
import { enableRuntimeCompiler } from "@/ext/runtimeCompiler"
import { SDK_VERSION, satisfiesRange } from "@/ext/sdk/version"
import { installCommandShortcuts } from "@/ext/commands"
import { createPackageStorage } from "@/ext/hostServices"
import { hideOverlay, loadPlacements, showOverlay, toggleOverlay } from "@/ext/overlays"
import { registerOverlaySettings } from "@/ext/overlaySettings"
import { contributionKey, extState, findPackage, runtime } from "@/ext/state"
import { useHostsStore } from "@/stores/hosts"
import { useLocaleStore } from "@/stores/locale"
import api from "@/utils/api"
import { wsUrl } from "@/utils/wsUrl"

// ``__kt_auth_retry`` is the api.js retry flag: a 401 rejects instead of opening a login dialog.
const QUIET = { __kt_auth_retry: true }
const TOKEN_MARGIN_SECONDS = 60

const defaultDeps = {
  importModule: (url) => import(/* @vite-ignore */ url),
  enableCompiler: enableRuntimeCompiler,
  builtinPackages: () => BUILTIN_UI_PACKAGES,
  fetchJson: async (url) => {
    const resp = await fetch(url, { credentials: "omit" })
    if (!resp.ok) throw new Error(`${resp.status} fetching ${url}`)
    return resp.json()
  },
  now: () => Date.now() / 1000,
}
const deps = { ...defaultDeps }
const _pending = new Map()
// package -> { value, expiresAt } for the current host
const _tokens = new Map()
let _reloadGeneration = 0

/** Replace I/O seams (module import, compiler load, JSON fetch, clock); for tests. */
export function setLoaderDeps(overrides = {}) {
  Object.assign(deps, defaultDeps, overrides)
}

function hostBase() {
  return useHostsStore().activeHost?.url || ""
}

/** Identity of the backend + user the current extension set belongs to. */
export function currentHostKey() {
  const active = useHostsStore().activeHost
  if (!active) return "same-origin"
  return `${active.id}|${active.currentUser?.id ?? ""}`
}

function encodePath(path) {
  return String(path || "")
    .replace(/^\/+/, "")
    .split("/")
    .map(encodeURIComponent)
    .join("/")
}

/** URL of a file under a package's UI root, carrying an asset token. */
export function assetUrlFor(pkg, path, token) {
  return `${hostBase()}/api/ext/${encodeURIComponent(pkg)}/ui/${token}/${encodePath(path)}`
}

/** The package's asset token, minted again when near expiry or ``force``d. */
async function assetToken(pkg, { force = false } = {}) {
  const cached = _tokens.get(pkg)
  if (!force && cached && cached.expiresAt - deps.now() > TOKEN_MARGIN_SECONDS) {
    return cached.value
  }
  const hostKey = extState.hostKey
  const { data } = await api.post("/extensions/ui/asset-token", { package: pkg })
  if (extState.hostKey === hostKey)
    _tokens.set(pkg, { value: data.token, expiresAt: data.expires_at })
  return data.token
}

/** Renew every asset token that is within the refresh margin of expiring. */
export async function refreshAssetTokens() {
  const due = [..._tokens.entries()].filter(
    ([, t]) => t.expiresAt - deps.now() <= TOKEN_MARGIN_SECONDS,
  )
  await Promise.all(due.map(([pkg]) => assetToken(pkg, { force: true }).catch(() => null)))
}

function initialStatus(record) {
  if (record.error) return { status: "error", loadError: record.error }
  if (!record.enabled) return { status: "disabled", loadError: "" }
  if (!satisfiesRange(SDK_VERSION, record.sdk)) {
    return {
      status: "incompatible",
      loadError: `needs UI SDK ${record.sdk}; this dashboard has ${SDK_VERSION}`,
    }
  }
  return { status: "idle", loadError: "" }
}

function removeStyles() {
  if (typeof document === "undefined") return
  for (const el of document.querySelectorAll("link[data-kt-ext]")) el.remove()
}

function injectStyles(record, token) {
  if (typeof document === "undefined") return
  for (const style of record.styles || []) {
    const link = document.createElement("link")
    link.rel = "stylesheet"
    link.href = assetUrlFor(record.package, style, token)
    link.dataset.ktExt = record.package
    document.head.appendChild(link)
  }
}

function interpolate(text, params = {}) {
  return String(text).replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? `{${name}}`))
}

async function loadBundles(record, locales, token) {
  for (const locale of locales) {
    const path = record.i18n?.[locale]
    if (!path || record.bundles[locale]) continue
    try {
      record.bundles[locale] = await deps.fetchJson(assetUrlFor(record.package, path, token))
    } catch (err) {
      console.warn(`[ui-extensions] ${record.package}: message bundle ${locale} failed`, err)
      record.bundles[locale] = {}
    }
  }
}

function createHandle(record) {
  const pkg = record.package
  const join = (path) => `/ext/${encodeURIComponent(pkg)}/${String(path || "").replace(/^\/+/, "")}`
  const provide = (kind) => (id, component, options) =>
    provideContributionComponent(pkg, kind, id, component, options)
  return {
    package: pkg,
    sdkVersion: SDK_VERSION,
    registerTab: provide("tab"),
    registerPanel: provide("panel"),
    registerRailGroup: provide("rail"),
    registerSettingsSection: provide("settings"),
    registerToolRenderer: provide("tool-renderer"),
    registerTriggerRenderer: provide("trigger-renderer"),
    registerWidgetRenderer: provide("widget"),
    registerOverlay: provide("overlay"),
    registerCommand: provide("command"),
    registerStatusItem: provide("status-item"),
    registerAction: provide("action"),
    registerMessageRenderer: provide("message-renderer"),
    registerMessageDecorator: provide("message-decorator"),
    storage: createPackageStorage(pkg),
    showOverlay: (id) => showOverlay(contributionKey(pkg, id)),
    hideOverlay: (id) => hideOverlay(contributionKey(pkg, id)),
    toggleOverlay: (id) => toggleOverlay(contributionKey(pkg, id)),
    api: {
      request: (config = {}) => api.request({ ...config, url: join(config.url) }),
      get: (path, config) => api.get(join(path), config),
      delete: (path, config) => api.delete(join(path), config),
      post: (path, data, config) => api.post(join(path), data, config),
      put: (path, data, config) => api.put(join(path), data, config),
      patch: (path, data, config) => api.patch(join(path), data, config),
    },
    ws: (path, protocols) => new WebSocket(wsUrl(`/api${join(path)}`), protocols),
    t(key, params) {
      const locale = useLocaleStore().locale
      const text = record.bundles[locale]?.[key] ?? record.bundles.en?.[key]
      return text == null ? key : interpolate(text, params)
    },
    assetUrl(path) {
      const token = _tokens.get(pkg)?.value
      if (!token) throw new Error(`${pkg}: no asset token (package not loaded from this host)`)
      return assetUrlFor(pkg, path, token)
    },
    openTab: (id) => openExtensionTab(`${pkg}/${id}`),
  }
}

async function loadPackage(pkg) {
  const record = findPackage(pkg)
  if (!record) throw new Error(`UI extension ${pkg} is not available on this host`)
  if (record.status === "loaded") return
  if (record.status === "disabled" || record.status === "incompatible" || record.error) {
    throw new Error(record.loadError || `UI extension ${pkg} is ${record.status}`)
  }
  const hostKey = extState.hostKey
  record.status = "loading"
  record.loadError = ""
  try {
    let mod
    if (record.builtin) {
      mod = await record.load()
    } else {
      let token = await assetToken(pkg)
      const locale = useLocaleStore().locale
      await loadBundles(record, [...new Set([locale, "en"])], token)
      if (!record.dev_url) injectStyles(record, token)
      await deps.enableCompiler()
      const entryUrl = (t) => record.dev_url || assetUrlFor(pkg, record.entry, t)
      try {
        mod = await deps.importModule(entryUrl(token))
      } catch (_first) {
        // A rejected token (e.g. expired, server restarted) gets one fresh retry.
        token = await assetToken(pkg, { force: true })
        mod = await deps.importModule(entryUrl(token))
      }
    }
    if (extState.hostKey !== hostKey) return
    const register = typeof mod?.default === "function" ? mod.default : mod?.register
    if (typeof register !== "function")
      throw new Error(`${pkg}: entry exports no register(kt) function`)
    record.handle = markRaw(createHandle(record))
    await register(record.handle)
    record.status = "loaded"
  } catch (err) {
    record.status = "error"
    record.loadError = err?.message || String(err)
    throw err
  }
}

/** Load a package's entry once per host; a failed load may be retried. */
export function ensurePackageLoaded(pkg) {
  const key = `${extState.hostKey}\u0000${pkg}`
  if (!_pending.has(key)) {
    const promise = loadPackage(pkg).catch((err) => {
      _pending.delete(key)
      throw err
    })
    _pending.set(key, promise)
  }
  return _pending.get(key)
}

/** Drop every contribution, style and cached load of the current host. */
export function unloadUiExtensions() {
  unregisterAllContributions()
  closeOwnerSocket({ forget: true })
  removeStyles()
  _pending.clear()
  _tokens.clear()
  extState.packages = []
}

/** Re-list the active host's UI packages; only the newest overlapping call applies. */
export async function reloadUiExtensions() {
  const generation = ++_reloadGeneration
  const hostKey = currentHostKey()
  unloadUiExtensions()
  extState.hostKey = hostKey
  loadPlacements(hostKey)
  extState.loading = true
  extState.error = ""
  let listed = []
  let error = ""
  try {
    const { data } = await api.get("/extensions/ui", QUIET)
    listed = data?.packages || []
  } catch (err) {
    error = err?.response?.data?.detail?.message || err?.message || String(err)
  }
  if (generation !== _reloadGeneration) return
  unregisterAllContributions()
  extState.error = error
  const builtins = deps
    .builtinPackages()
    .map((p) => ({ ...p, builtin: true, enabled: true, error: "" }))
  extState.packages = [...builtins, ...listed].map((p) => ({
    ...p,
    ...initialStatus(p),
    handle: null,
    bundles: { ...(p.messages || {}) },
  }))
  for (const record of extState.packages) {
    if (record.status === "idle") registerPackageContributions(record)
  }
  extState.loading = false
}

/** Enable, disable, or reset (``null``) a package's UI, then reload. */
export async function setUiExtensionEnabled(pkg, enabled) {
  await api.put(`/extensions/ui/${encodeURIComponent(pkg)}`, { enabled })
  await reloadUiExtensions()
}

let _started = false

/** Start the loader: follow host/user switches and locale changes. Idempotent. */
export function startUiExtensions() {
  if (_started) return
  _started = true
  registerOverlaySettings()
  installCommandShortcuts()
  runtime.ensureLoaded = ensurePackageLoaded
  watch(currentHostKey, () => reloadUiExtensions(), { immediate: true })
  watch(
    () => useLocaleStore().locale,
    async (locale) => {
      const loaded = extState.packages.filter((r) => r.status === "loaded" && r.i18n?.[locale])
      for (const record of loaded)
        await loadBundles(record, [locale], await assetToken(record.package))
    },
  )
  setInterval(() => refreshAssetTokens(), TOKEN_MARGIN_SECONDS * 500)
}
