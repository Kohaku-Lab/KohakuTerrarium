/**
 * Bridges UI contributions into the dashboard's own registries.
 *
 * Each declared contribution gets a host component registered at once
 * (tab kind, layout panel, rail group, settings section, or in-chat
 * renderer), so its launcher exists before any package code loads. The
 * host renders the real component once the entry has called ``kt.register*``.
 */

import { defineComponent, h, markRaw } from "vue"

import ExtensionContribution from "@/ext/ExtensionContribution.vue"
import { clearActions, registerAction } from "@/ext/actions"
import { clearDecorators, registerDecorator } from "@/ext/decorators"
import { clearDockPanels, registerDockPanel } from "@/ext/dockPanels"
import { clearFileViews, registerFileView } from "@/ext/fileViews"
import { clearNotificationChannels, registerNotificationChannel } from "@/ext/notificationChannels"
import { clearTrayItems, registerTrayItem } from "@/ext/trayItems"
import {
  GRAPH_NODE_KINDS,
  clearGraphSlots,
  registerGraphBadge,
  registerGraphPanel,
} from "@/ext/graphSlots"
import { registerCommand, unregisterCommands } from "@/ext/commands"
import { clearOverlays, overlays, registerOverlay } from "@/ext/overlays"
import { registerToolRenderer, registerTriggerRenderer, unregisterRenderers } from "@/ext/renderers"
import {
  clearMessageRenderers,
  messageRenderers,
  registerMessageRenderer,
} from "@/ext/messageRenderers"
import { clearStatusItems, registerStatusItem } from "@/ext/statusItems"
import {
  registerWidgetRenderer,
  unregisterWidgetRenderersByKey,
  widgetRenderers,
} from "@/ext/widgets"
import {
  contributionComponents,
  contributionKey,
  extState,
  findContribution,
  findPackage,
  parseContributionKey,
} from "@/ext/state"
import { useLayoutStore } from "@/stores/layout"
import {
  registerSettingsSection,
  unregisterSettingsSection,
} from "@/stores/settingsSectionRegistry"
import { railGroups, registerRailGroup, registerTabKind, tabKinds } from "@/stores/tabKindRegistry"
import { useTabsStore } from "@/stores/tabs"

const _registered = []

/** Owner kind each mount kind requires (mirrors ``packages/ui_slot.py``); ``any`` takes every owner kind. */
export const MOUNT_OWNER_KINDS = {
  settings: "package",
  tab: "package",
  rail: "package",
  panel: "plugin",
  "tool-renderer": "tool",
  "trigger-renderer": "trigger",
  widget: "any",
  overlay: "package",
  command: "package",
  "status-item": "package",
  action: "package",
  "message-renderer": "any",
  "message-decorator": "package",
  "file-view": "package",
  "graph-badge": "package",
  "graph-panel": "package",
  "tray-item": "package",
  "notification-channel": "package",
}

const HANDLER_KINDS = ["command", "action", "tray-item", "notification-channel"]

function fileViewOptions(source) {
  const out = {}
  if (Array.isArray(source?.patterns)) out.patterns = [...source.patterns]
  if (source?.defaultView !== undefined) out.defaultView = source.defaultView
  return out
}

function graphOptions(source) {
  const nodes =
    Array.isArray(source?.nodes) && source.nodes.length ? source.nodes : GRAPH_NODE_KINDS
  return { nodes: [...nodes] }
}

function decoratorOptions(source) {
  const out = {}
  if (source?.position !== undefined) out.position = source.position
  if (source?.maxHeight !== undefined) out.maxHeight = source.maxHeight
  if (Array.isArray(source?.roles)) out.roles = [...source.roles]
  return out
}

function actionOptions(source) {
  const out = {}
  if (Array.isArray(source?.targets)) out.targets = [...source.targets]
  if (Array.isArray(source?.roles)) out.roles = [...source.roles]
  return out
}

const STATUS_OPTION_NAMES = ["side", "maxWidth"]

function statusOptions(source) {
  const out = {}
  for (const name of STATUS_OPTION_NAMES) if (source?.[name] !== undefined) out[name] = source[name]
  return out
}

const OVERLAY_OPTION_NAMES = [
  "anchor",
  "surface",
  "alwaysOnTop",
  "width",
  "height",
  "draggable",
  "resizable",
  "clickThrough",
  "defaultVisible",
]

function overlayOptions(source) {
  const out = {}
  for (const name of OVERLAY_OPTION_NAMES)
    if (source?.[name] !== undefined) out[name] = source[name]
  return out
}

const OWNER_KINDS = ["package", "plugin", "tool", "trigger"]
const RENDERER_ID = /^[a-z0-9][a-z0-9_-]{0,31}(\.[a-z0-9][a-z0-9_-]{0,31}){1,3}$/
const RESERVED_RENDERER_PREFIXES = ["kt.", "mcp."]

/** Whether ``id`` is a renderer id a package may provide. */
export function isPackageRendererId(id) {
  return (
    typeof id === "string" &&
    RENDERER_ID.test(id) &&
    !RESERVED_RENDERER_PREFIXES.some((prefix) => id.startsWith(prefix))
  )
}

function ownerAllowed(kind, owner, pkg) {
  const ownerKind = MOUNT_OWNER_KINDS[kind]
  const [prefix, ...rest] = String(owner || "").split(":")
  if (!rest.length || !rest.join(":")) return false
  if (prefix === "package" && owner !== `package:${pkg}`) return false
  return ownerKind === "any" ? OWNER_KINDS.includes(prefix) : prefix === ownerKind
}

function ownerName(owner) {
  return String(owner || "").slice(String(owner || "").indexOf(":") + 1)
}

function hostFor(pkg, contribution) {
  return markRaw(
    defineComponent({
      name: `KtExtensionHost`,
      inheritAttrs: false,
      setup(_props, { attrs }) {
        return () => h(ExtensionContribution, { ...attrs, pkg, contribution })
      },
    }),
  )
}

/** Register one contribution's host into the core registry for its kind. */
export function registerContribution(pkg, contribution) {
  const key = contributionKey(pkg, contribution.id)
  const host = hostFor(pkg, contribution)
  switch (contribution.kind) {
    case "tab":
      registerTabKind({
        kind: key,
        component: host,
        minDensity: contribution.minDensity || "compact",
        capabilities: { extension: true },
      })
      break
    case "panel":
      useLayoutStore().registerPanel({
        id: key,
        label: contribution.label,
        description: contribution.description || "",
        component: host,
      })
      if (contribution.dock) {
        registerDockPanel({
          key,
          label: contribution.label,
          icon: contribution.icon,
          order: contribution.order,
          component: host,
        })
      }
      break
    case "rail":
      registerRailGroup({ id: key, component: host, order: contribution.order ?? 100 })
      break
    case "settings":
      registerSettingsSection({
        id: key,
        label: contribution.label,
        order: contribution.order ?? 100,
        component: host,
      })
      break
    case "tool-renderer":
    case "trigger-renderer": {
      const register =
        contribution.kind === "tool-renderer" ? registerToolRenderer : registerTriggerRenderer
      if (!register(ownerName(contribution.owner), { key, pkg, contribution, component: host }))
        return null
      break
    }
    case "widget":
      if (!isPackageRendererId(contribution.renderer)) return null
      if (!registerWidgetRenderer(contribution.renderer, host, { key, trust: "package" }))
        return null
      break
    case "overlay":
      registerOverlay({
        key,
        pkg,
        id: contribution.id,
        label: contribution.label,
        component: host,
        options: overlayOptions(contribution.overlay),
      })
      break
    case "command":
      registerCommand(pkg, contribution, key)
      break
    case "status-item":
      registerStatusItem({
        key,
        pkg,
        id: contribution.id,
        label: contribution.label,
        order: contribution.order,
        component: host,
        options: statusOptions(contribution.status),
      })
      break
    case "tray-item":
      registerTrayItem({ key, pkg, label: contribution.label, order: contribution.order })
      break
    case "notification-channel":
      registerNotificationChannel({ key, pkg, label: contribution.label })
      break
    case "file-view":
      registerFileView({
        key,
        pkg,
        id: contribution.id,
        label: contribution.label,
        icon: contribution.icon,
        order: contribution.order,
        component: host,
        options: fileViewOptions(contribution.fileView),
      })
      break
    case "graph-badge":
    case "graph-panel": {
      const register = contribution.kind === "graph-badge" ? registerGraphBadge : registerGraphPanel
      register({
        key,
        pkg,
        id: contribution.id,
        label: contribution.label,
        order: contribution.order,
        component: host,
        options: graphOptions(contribution.graph),
      })
      break
    }
    case "message-decorator":
      registerDecorator({
        key,
        pkg,
        id: contribution.id,
        label: contribution.label,
        order: contribution.order,
        component: host,
        options: decoratorOptions(contribution.decorator),
      })
      break
    case "message-renderer":
      if (!isPackageRendererId(contribution.renderer)) return null
      if (!registerMessageRenderer(contribution.renderer, host, { key })) return null
      break
    case "action":
      registerAction({
        key,
        pkg,
        id: contribution.id,
        label: contribution.label,
        icon: contribution.icon,
        order: contribution.order,
        options: actionOptions(contribution.action),
      })
      break
    default:
      return null
  }
  _registered.push({ kind: contribution.kind, key })
  return key
}

/** Register every declared contribution of a listed package. */
export function registerPackageContributions(record) {
  for (const contribution of record.contributes || [])
    registerContribution(record.package, contribution)
}

/** Remove every contribution registered through this module. */
export function unregisterAllContributions() {
  const layout = useLayoutStore()
  const removed = _registered.splice(0)
  for (const { kind, key } of removed) {
    if (kind === "tab") tabKinds.delete(key)
    else if (kind === "panel") layout.unregisterPanel(key)
    else if (kind === "rail") railGroups.delete(key)
    else if (kind === "settings") unregisterSettingsSection(key)
  }
  clearOverlays()
  clearStatusItems()
  clearActions()
  clearMessageRenderers()
  clearDecorators()
  clearDockPanels()
  clearFileViews()
  clearGraphSlots()
  clearTrayItems()
  clearNotificationChannels()
  unregisterCommands(removed.filter((r) => r.kind === "command").map((r) => r.key))
  unregisterRenderers(removed.map((r) => r.key))
  unregisterWidgetRenderersByKey(removed.map((r) => r.key))
  contributionComponents.clear()
}

/** Keys of every currently registered contribution, in registration order. */
export function registeredContributionKeys() {
  return _registered.map((r) => r.key)
}

/**
 * Store the component a package provides for ``kind``/``id``. An id the
 * manifest did not declare is registered on the fly as an inline contribution.
 */
export function provideContributionComponent(pkg, kind, id, component, options = {}) {
  if (!component) throw new Error(`${pkg}: register${kind} ${id} needs a component`)
  if (HANDLER_KINDS.includes(kind) && typeof component !== "function") {
    throw new Error(`${pkg}: ${kind} ${id} needs a handler function`)
  }
  if (kind === "file-view" && !findContribution(pkg, id) && !options.patterns?.length) {
    throw new Error(`${pkg}: an undeclared file-view ${id} needs options.patterns`)
  }
  if (kind === "action" && !findContribution(pkg, id) && !options.targets?.length) {
    throw new Error(`${pkg}: an undeclared action ${id} needs options.targets`)
  }
  const declared = findContribution(pkg, id)
  if (declared && declared.kind !== kind) {
    throw new Error(`${pkg}: ${id} is declared as a ${declared.kind}, not a ${kind}`)
  }
  if (!declared) {
    const record = findPackage(pkg)
    const ownerKind = MOUNT_OWNER_KINDS[kind]
    const owner =
      options.owner || (ownerKind === "package" || ownerKind === "any" ? `package:${pkg}` : "")
    if (!ownerAllowed(kind, owner, pkg)) {
      throw new Error(
        `${pkg}: an undeclared ${kind} ${id} needs options.owner "${ownerKind}:<name>"`,
      )
    }
    if (
      (kind === "widget" || kind === "message-renderer") &&
      !isPackageRendererId(options.renderer)
    ) {
      throw new Error(
        `${pkg}: ${kind} ${id} needs options.renderer, a dotted id outside kt./mcp. (got ${options.renderer})`,
      )
    }
    const contribution = {
      kind,
      id,
      owner,
      label: options.label || id,
      icon: options.icon || "",
      description: options.description || "",
      order: options.order ?? 100,
      minDensity: options.minDensity || "compact",
      isolation: "inline",
      ...(kind === "widget" || kind === "message-renderer" ? { renderer: options.renderer } : {}),
      ...(kind === "overlay" ? { overlay: overlayOptions(options) } : {}),
      ...(kind === "status-item" ? { status: statusOptions(options) } : {}),
      ...(kind === "action" ? { action: actionOptions(options) } : {}),
      ...(kind === "message-decorator" ? { decorator: decoratorOptions(options) } : {}),
      ...(kind === "panel" ? { dock: options.dock === true } : {}),
      ...(kind === "file-view" ? { fileView: fileViewOptions(options) } : {}),
      ...(kind === "graph-badge" || kind === "graph-panel" ? { graph: graphOptions(options) } : {}),
      ...(kind === "command"
        ? { shortcut: options.shortcut || "", keywords: options.keywords || "" }
        : {}),
    }
    if (record) record.contributes = [...(record.contributes || []), contribution]
    registerContribution(pkg, contribution)
  }
  contributionComponents.set(contributionKey(pkg, id), markRaw(component))
  if (kind === "widget") updateWidgetEntry(contributionKey(pkg, id), options)
  if (kind === "message-renderer") updateMessageRendererEntry(contributionKey(pkg, id), options)
  if (kind === "overlay") {
    const entry = overlays.get(contributionKey(pkg, id))
    if (entry) entry.options = { ...entry.options, ...overlayOptions(options) }
  }
}

/** Apply a package's ``version`` option to its registered message renderer. */
function updateMessageRendererEntry(key, options) {
  for (const entry of messageRenderers.values()) {
    if (entry.key === key && Number.isInteger(options.version) && options.version >= 1) {
      entry.version = options.version
    }
  }
}

/** Apply a package's ``version`` / ``surfaces`` / chrome options to its registered renderer. */
function updateWidgetEntry(key, options) {
  for (const entry of widgetRenderers.values()) {
    if (entry.key !== key) continue
    if (Number.isInteger(options.version) && options.version >= 1) entry.version = options.version
    if (Array.isArray(options.surfaces) && options.surfaces.length) {
      entry.surfaces = [...options.surfaces]
    }
    if (typeof options.summary === "function") entry.summary = options.summary
    if (typeof options.icon === "function") entry.icon = options.icon
  }
}

/** Label and icon of an extension tab kind, or ``null`` for a non-extension kind. */
export function extensionTabMeta(kind) {
  const parts = parseContributionKey(kind)
  if (!parts || !String(kind).startsWith("ext:")) return null
  const c = findContribution(parts.pkg, parts.id)
  return c ? { label: c.label, icon: c.icon || "i-carbon-application" } : null
}

/** Every tab contribution on the active host, for the new-tab menu. */
export function extensionTabLaunchers() {
  const out = []
  for (const record of extState.packages) {
    if (!record.enabled || record.status === "incompatible") continue
    for (const c of record.contributes || []) {
      if (c.kind === "tab")
        out.push({
          key: contributionKey(record.package, c.id),
          label: c.label,
          icon: c.icon || "i-carbon-application",
          package: record.package,
        })
    }
  }
  return out
}

/** Open ``"<package>/<id>"`` (or its ``ext:`` key) as a tab. */
export function openExtensionTab(target) {
  const parts = parseContributionKey(target)
  if (!parts) throw new Error(`not an extension tab: ${target}`)
  const key = contributionKey(parts.pkg, parts.id)
  const c = findContribution(parts.pkg, parts.id)
  return useTabsStore().openTab({ kind: key, id: key, label: c?.label || parts.id })
}
