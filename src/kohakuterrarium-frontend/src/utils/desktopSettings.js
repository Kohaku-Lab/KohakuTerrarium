import { useTabsStore } from "@/stores/tabs"

const SECTION_ID = "desktop-app-settings"
const MAX_FRAMES = 40

/** Open the settings tab and scroll to the desktop app section once it has rendered. */
export function openDesktopSettings() {
  const tabs = useTabsStore()
  tabs.openTab({ kind: "settings", id: "settings" })
  tabs.activateTab("settings")
  let frames = 0
  const reveal = () => {
    const section = document.getElementById(SECTION_ID)
    if (section) {
      section.scrollIntoView({ block: "start" })
      return
    }
    frames += 1
    if (frames < MAX_FRAMES) requestAnimationFrame(reveal)
  }
  requestAnimationFrame(reveal)
}
