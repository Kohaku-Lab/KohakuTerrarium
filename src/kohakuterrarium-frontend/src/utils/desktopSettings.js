import { openSettingsSection } from "@/stores/settingsNav"

const SECTION_ID = "desktop-app-settings"

/** Open the Desktop section of the settings tab. */
export function openDesktopSettings() {
  openSettingsSection("desktop", { anchor: SECTION_ID })
}
