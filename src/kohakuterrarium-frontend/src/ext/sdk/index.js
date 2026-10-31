/**
 * ``@kohakuterrarium/ui-sdk`` v1: the only contract between the dashboard
 * and package UI code. Package code imports it by its bare name; the page
 * import map points that name at ``/ext-shared/sdk.js``, which re-exports
 * this module. App stores and components are not part of the contract.
 *
 * Registration lives on the per-package handle (``kt``) passed to the
 * entry's ``register(kt)``; the composables below read dashboard context
 * from inside contribution components.
 */

import { computed, getCurrentInstance, inject, onUnmounted } from "vue"
import { ElMessage, ElMessageBox } from "element-plus"

import {
  beginDrag,
  hideOverlay,
  isOverlayVisible,
  moveOverlay,
  overlayBounds,
  resizeOverlay,
  showOverlay,
  toggleOverlay,
} from "@/ext/overlays"
import { windowLabel } from "@/ext/overlayWindows"
import { openExtensionTab } from "@/ext/registry"
import {
  EXTENSION_INJECT_KEY,
  OVERLAY_INJECT_KEY,
  OVERLAY_MODE_INJECT_KEY,
  OWNER_INJECT_KEY,
} from "@/ext/sdk/keys"
import { THEME_TOKENS } from "@/ext/sdk/theme"
import { injectScope } from "@/composables/useScope"
import { useChatStore } from "@/stores/chat"
import { useHostsStore } from "@/stores/hosts"
import { useInstancesStore } from "@/stores/instances"
import { useLocaleStore } from "@/stores/locale"
import { useTabsStore } from "@/stores/tabs"
import { useThemeStore } from "@/stores/theme"
import { closeOverlayWindow, setOverlayBounds, startOverlayDrag } from "@/utils/hostBridge"

export { OwnerCallError } from "@/ext/ownerClient"
export { useWidget } from "@/components/chat/widgets/useWidget"
export { SDK_VERSION, satisfiesRange } from "@/ext/sdk/version"
export {
  FRAME_MESSAGES,
  FRAME_PROTOCOL,
  FRAME_SUPPORTED,
  FrameIsolationUnsupportedError,
  createFrameBridge,
} from "@/ext/sdk/frameBridge"
export { THEME_TOKENS }

/** Identity helper that documents an entry's ``register(kt)`` function. */
export function defineExtension(register) {
  return register
}

/** The ``kt`` handle of the package that owns the calling component. */
export function useExtension() {
  const kt = inject(EXTENSION_INJECT_KEY, null)
  if (!kt) throw new Error("useExtension() must be called inside an extension contribution")
  return kt
}

/**
 * Talk to the module that owns the calling contribution (package, plugin,
 * tool or trigger). ``state`` follows ``ui_state()`` and ``"state"`` pushes;
 * ``call(action, args)`` runs ``ui_call``; ``on(event, fn)`` receives every
 * other ``ui_emit`` push and is removed with the component.
 */
export function useOwner() {
  const client = inject(OWNER_INJECT_KEY, null)
  if (!client) throw new Error("useOwner() must be called inside an extension contribution")
  client.start()
  const hasInstance = Boolean(getCurrentInstance())
  return {
    owner: client.owner,
    state: client.state,
    status: client.status,
    error: client.error,
    call: client.call,
    refresh: client.refresh,
    on(event, handler) {
      const off = client.on(event, handler)
      if (hasInstance) onUnmounted(off)
      return off
    },
  }
}

/** `useOverlay()` inside a native overlay window: the window is the overlay, positions are screen pixels. */
function useWindowOverlay(key) {
  const size = () => ({ width: window.innerWidth, height: window.innerHeight })
  const hide = async () => {
    hideOverlay(key)
    await closeOverlayWindow(windowLabel(key))
  }
  return {
    visible: computed(() => true),
    bounds: computed(() => ({ left: window.screenX, top: window.screenY, ...size() })),
    show: () => {},
    hide,
    toggle: hide,
    move: (left, top) => setOverlayBounds(left, top, size().width, size().height),
    resize: (width, height) => setOverlayBounds(window.screenX, window.screenY, width, height),
    startDrag: (event) => {
      event?.preventDefault?.()
      return startOverlayDrag()
    },
  }
}

/**
 * Control the overlay the calling component is drawn in: ``visible`` and
 * ``bounds`` are refs; ``startDrag(event)`` moves a click-through overlay
 * from a ``pointerdown`` on any element of its own. In a native window
 * overlay ``move`` and ``resize`` use screen pixels.
 */
export function useOverlay() {
  const key = inject(OVERLAY_INJECT_KEY, null)
  if (!key) throw new Error("useOverlay() must be called inside an overlay contribution")
  if (inject(OVERLAY_MODE_INJECT_KEY, "app") === "window") return useWindowOverlay(key)
  return {
    visible: computed(() => isOverlayVisible(key)),
    bounds: computed(() => overlayBounds(key)),
    show: () => showOverlay(key),
    hide: () => hideOverlay(key),
    toggle: () => toggleOverlay(key),
    move: (left, top) => moveOverlay(key, left, top),
    resize: (width, height) => resizeOverlay(key, width, height),
    startDrag: (event) => beginDrag(key, event, "move"),
  }
}

/** The backend host this dashboard is talking to. */
export function useHost() {
  const hosts = useHostsStore()
  return computed(() => {
    const active = hosts.activeHost
    if (!active) return { id: null, name: "", url: "", sameOrigin: true }
    return { id: active.id, name: active.name, url: active.url, sameOrigin: false }
  })
}

/** The session the calling component is mounted in, or ``null`` outside one. */
export function useSession() {
  const scope = injectScope()
  const instances = useInstancesStore()
  return computed(() => {
    if (!scope) return null
    const match = (it) => it && (it.id === scope || it.graph_id === scope)
    const instance = match(instances.current)
      ? instances.current
      : instances.list.find(match) || null
    const creatures = (instance?.creatures || []).map((c) => ({
      id: c.creature_id || c.agent_id || c.name,
      name: c.name || c.creature_id,
    }))
    return { id: scope, instance, creatures }
  })
}

/** The focused creature of the surrounding session, or ``null``. */
export function useCreature() {
  const scope = injectScope()
  const chat = useChatStore(scope)
  return computed(() => {
    if (!scope) return null
    const name = chat.terrariumTarget
    return name ? { name, model: chat.activeCreatureInfo?.model || "" } : null
  })
}

/** Read-only events of the focused chat tab in the surrounding session. */
export function useSessionEvents() {
  const scope = injectScope()
  const chat = useChatStore(scope)
  return computed(() => {
    if (!scope || !chat.activeTab) return []
    return chat.eventsByTab?.[chat.activeTab] || []
  })
}

/** The active UI locale (``"en"``, ``"zh-TW"``, …). */
export function useLocale() {
  const locale = useLocaleStore()
  return computed(() => locale.locale)
}

/** Whether the dashboard is in dark mode; colors come from ``THEME_TOKENS``. */
export function useTheme() {
  const theme = useThemeStore()
  return { dark: computed(() => theme.dark), tokens: THEME_TOKENS }
}

export { hostInfo, notifyDesktop, openExternal } from "@/ext/hostServices"

/** Show a toast. ``type`` is ``info`` | ``success`` | ``warning`` | ``error``. */
export function notify(message, type = "info") {
  ElMessage({ message: String(message), type })
}

/** Ask a yes/no question; resolves ``true`` when confirmed. */
export async function confirm(message, title = "", options = {}) {
  try {
    await ElMessageBox.confirm(String(message), title, { type: "warning", ...options })
    return true
  } catch (_cancelled) {
    return false
  }
}

/**
 * Open a tab. ``"<package>/<id>"`` opens a package tab contribution;
 * an object is a core tab spec such as ``{ kind: "settings", id: "settings" }``.
 */
export function openTab(target) {
  if (typeof target === "string") return openExtensionTab(target)
  return useTabsStore().openTab(target)
}
