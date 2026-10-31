/**
 * Settings registry: groups and the sections inside them.
 *
 * A section is { id, label | labelKey, icon, group, order, keywords, scope, width, visible, badge,
 * component }. ``order`` ranks a section inside its group only; ``width`` is "form", "wide" or
 * "fill" (the section manages its own scrolling). ``inline`` sections are built in and rendered
 * by SettingsPage; every other section renders its ``component``.
 */

import { markRaw, reactive } from "vue"

import {
  BUILTIN_GROUPS,
  BUILTIN_SECTIONS,
  EXTENSIONS_GROUP,
} from "@/components/settings/builtinSections"

export { EXTENSIONS_GROUP }

export const settingsGroups = reactive(new Map())
export const settingsSections = reactive(new Map())

export function registerSettingsGroup({ id, label = "", labelKey = "", order = 100 }) {
  if (!id) return
  settingsGroups.set(id, { id, label: label || id, labelKey, order })
}

export function unregisterSettingsGroup(id) {
  settingsGroups.delete(id)
}

for (const group of BUILTIN_GROUPS) registerSettingsGroup(group)

export function registerSettingsSection({
  id,
  label = "",
  labelKey = "",
  descriptionKey = "",
  description = "",
  icon = "i-carbon-settings",
  group = EXTENSIONS_GROUP,
  order = 100,
  keywords = [],
  scope = "global",
  width = "form",
  component = null,
  inline = false,
  visible = null,
  badge = null,
}) {
  if (!id) return
  if (!inline && !component) throw new Error(`settings section ${id} needs a component`)
  settingsSections.set(id, {
    id,
    label: label || id,
    labelKey,
    descriptionKey,
    description,
    icon,
    group,
    order,
    keywords,
    scope,
    width,
    component: component ? markRaw(component) : null,
    inline,
    visible,
    badge,
  })
}

export function unregisterSettingsSection(id) {
  settingsSections.delete(id)
}

/** Visible sections in display order: group order, then section order (ties keep registration order). */
export function visibleSettingsSections() {
  return visibleSettingsGroups().flatMap((group) => group.sections)
}

/** Groups that have at least one visible section, each with its sections in order. */
export function visibleSettingsGroups() {
  const visible = [...settingsSections.values()].filter((s) => !s.visible || s.visible())
  const byGroup = new Map()
  for (const section of visible) {
    const id = settingsGroups.has(section.group) ? section.group : EXTENSIONS_GROUP
    if (!byGroup.has(id)) byGroup.set(id, [])
    byGroup.get(id).push(section)
  }
  return [...settingsGroups.values()]
    .filter((group) => byGroup.has(group.id))
    .sort((a, b) => a.order - b.order)
    .map((group) => ({
      ...group,
      sections: byGroup.get(group.id).sort((a, b) => a.order - b.order),
    }))
}

/** Register the built-in groups and sections. Idempotent. */
export function registerBuiltinSettingsSections() {
  for (const group of BUILTIN_GROUPS) {
    if (!settingsGroups.has(group.id)) registerSettingsGroup(group)
  }
  for (const section of BUILTIN_SECTIONS) {
    if (!settingsSections.has(section.id)) registerSettingsSection({ ...section, inline: true })
  }
}
