import { createApp } from "vue"
import { createPinia } from "pinia"
import { createRouter, createWebHistory } from "vue-router"
import { routes } from "vue-router/auto-routes"
import App from "./App.vue"

import { registerBuiltinPanels } from "@/stores/layoutPanels"
import { ensureUIPrefsLoaded } from "@/utils/uiPrefs"

import "element-plus/es/components/message/style/css"
import "element-plus/es/components/message-box/style/css"
import "element-plus/es/components/notification/style/css"
import "uno.css"
import "./style.css"

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// Backward-compat guard for bookmarks to retired URLs. v1's NavRail
// page-per-route tree (`/instances/<id>`, `/sessions`, `/settings`,
// `/registry`, `/studio/*`, `/new`) and the `/mobile/*` mobile page
// tree are gone — anyone hitting those gets quietly sent to "/" so
// MacroShell can take over and restore tabs from persistence.
const RETIRED_PREFIXES = [
  "/mobile",
  "/instances",
  "/sessions",
  "/settings",
  "/registry",
  "/studio",
  "/new",
]
router.beforeEach((to) => {
  if (RETIRED_PREFIXES.some((p) => to.path === p || to.path.startsWith(p + "/"))) {
    return "/"
  }
  return undefined
})

async function bootstrap() {
  await ensureUIPrefsLoaded()

  const pinia = createPinia()
  const app = createApp(App)

  app.use(pinia)
  app.use(router)

  registerBuiltinPanels()

  app.mount("#app")
}

bootstrap()
