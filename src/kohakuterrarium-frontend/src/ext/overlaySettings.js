import OverlaysSettings from "@/ext/OverlaysSettings.vue"
import { overlays } from "@/ext/overlays"
import { registerSettingsSection } from "@/stores/settingsSectionRegistry"

export const OVERLAY_SETTINGS_ID = "overlays"

/** Add the "Overlays" settings section; it shows only while an overlay is registered. */
export function registerOverlaySettings() {
  registerSettingsSection({
    id: OVERLAY_SETTINGS_ID,
    label: "Overlays",
    order: 95,
    component: OverlaysSettings,
    visible: () => overlays.size > 0,
  })
}
