import { usePaletteStore } from "@/stores/palette"
import { openDesktopSettings } from "@/utils/desktopSettings"
import {
  exportDiagnostics,
  hostKind,
  hostSupports,
  openLogs,
  restartBackend,
} from "@/utils/hostBridge"

export const DESKTOP_COMMAND_IDS = {
  settings: "desktop:settings",
  restart: "desktop:restart-backend",
  diagnostics: "desktop:export-diagnostics",
  logs: "desktop:open-logs",
  print: "desktop:print",
}

/** Offer the desktop shell's own actions in the command palette; nothing outside the Tauri shell. */
export function useDesktopCommands() {
  if (hostKind() !== "tauri" || !hostSupports("restart_backend")) return
  const palette = usePaletteStore()
  const entries = [
    [DESKTOP_COMMAND_IDS.settings, "Desktop: settings", "i-carbon-screen", openDesktopSettings],
    [DESKTOP_COMMAND_IDS.restart, "Desktop: restart backend", "i-carbon-restart", restartBackend],
    [
      DESKTOP_COMMAND_IDS.diagnostics,
      "Desktop: export diagnostics",
      "i-carbon-document-export",
      () => exportDiagnostics(),
    ],
    [DESKTOP_COMMAND_IDS.logs, "Desktop: open backend log", "i-carbon-catalog", openLogs],
    [
      DESKTOP_COMMAND_IDS.print,
      "Desktop: print this conversation",
      "i-carbon-printer",
      () => window.print(),
    ],
  ]
  for (const [id, label, icon, run] of entries) {
    palette.register({ id, label, icon, keywords: "desktop shell", handler: () => void run() })
  }
}
