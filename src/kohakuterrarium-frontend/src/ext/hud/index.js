import HudOverlay from "@/ext/hud/HudOverlay.vue"
import { HUD_KEY } from "@/ext/hud/hudModel"
import { registerOverlay, toggleOverlay } from "@/ext/overlays"

/** Add the agents HUD to the overlay registry: a desktop window where the shell has them, else in the app. */
export function registerHud() {
  registerOverlay({
    key: HUD_KEY,
    pkg: "KohakuTerrarium",
    id: "hud",
    label: "Agents",
    component: HudOverlay,
    options: {
      surface: "window",
      anchor: "top-right",
      width: 300,
      height: 380,
      defaultVisible: false,
      firstParty: true,
    },
  })
}

export function toggleHud() {
  toggleOverlay(HUD_KEY)
}
