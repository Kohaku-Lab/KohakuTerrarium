/**
 * useBuiltinCommands — register the canonical palette commands. Call
 * once from App.vue after the layout + palette stores are ready.
 *
 * Adds one command per preset (mode: <preset>), per panel
 * (panel: open <panel>), plus layout edit/save-as and debug
 * shortcuts.
 */

import { watchEffect } from "vue"

import { useLayoutStore } from "@/stores/layout"
import { usePaletteStore } from "@/stores/palette"
import { fireLayoutEditRequested, fireLayoutSaveAsRequested } from "@/utils/layoutEvents"

export function useBuiltinCommands() {
  const layout = useLayoutStore()
  const palette = usePaletteStore()

  // Core (static) commands. Registered once.
  palette.register({
    id: "layout:edit",
    label: "Layout: enter edit mode",
    icon: "i-carbon-settings-edit",
    keywords: "customize panels",
    shortcut: "Ctrl+Shift+L",
    handler: () => fireLayoutEditRequested(),
  })
  palette.register({
    id: "layout:save-as",
    label: "Layout: save current as new preset",
    icon: "i-carbon-save",
    keywords: "preset",
    handler: () => fireLayoutSaveAsRequested(),
  })
  palette.register({
    id: "layout:reset-current",
    label: "Layout: reset current preset to default",
    icon: "i-carbon-reset",
    handler: () => {
      const id = layout.activePresetId
      if (id) layout.resetPresetToDefault(id)
    },
  })

  // Dynamic commands: one per preset, one per panel. We refresh the
  // registrations whenever the list changes.
  watchEffect(() => {
    for (const preset of Object.values(layout.allPresets)) {
      if (!preset?.id || preset.id.startsWith("legacy-")) continue
      palette.register({
        id: `mode:${preset.id}`,
        label: `Mode: ${preset.label || preset.id}`,
        icon: "i-carbon-layout",
        keywords: `preset ${preset.id}`,
        shortcut: preset.shortcut || "",
        handler: () => layout.switchPreset(preset.id),
      })
    }

    for (const panel of layout.panelList) {
      if (!panel?.id || panel.id === "status-bar") continue
      palette.register({
        id: `panel:${panel.id}`,
        label: `Panel: ${panel.label || panel.id}`,
        icon: "i-carbon-panel-expansion",
        keywords: `open ${panel.id}`,
        handler: () => {
          // Add the panel to its preferred zone in the active preset.
          const target = (panel.preferredZones && panel.preferredZones[0]) || "main"
          layout.addSlotToZone(target, panel.id)
        },
      })
    }
  })

  // Debug shortcuts.
  palette.register({
    id: "debug:open-logs",
    label: "Debug: open logs tab",
    icon: "i-carbon-catalog",
    handler: () => {
      layout.switchPreset("debug")
    },
  })
}
