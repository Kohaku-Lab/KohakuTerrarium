import { createApp } from "vue"
import { createPinia } from "pinia"
import { createRouter, createWebHistory } from "vue-router"
import { routes } from "vue-router/auto-routes"
import App from "./App.vue"

import { startUiExtensions } from "@/ext/loader"
import OverlayWindowApp from "@/ext/OverlayWindowApp.vue"
import { overlayKeyFromSearch, startOverlayWindows } from "@/ext/overlayWindows"
import { installSharedModules } from "@/ext/shared"
import { initializeAttentionPrefs } from "@/stores/attentionPrefs"
import { registerBuiltinPanels } from "@/stores/layoutPanels"
import { useLocaleStore } from "@/stores/locale"
import { useThemeStore } from "@/stores/theme"
import PopoutApp from "@/components/shell/PopoutApp.vue"
import { installExternalLinkGuard } from "@/utils/externalLinks"
import { popoutScopeFromSearch } from "@/utils/popout"
import { ensureUIPrefsLoaded } from "@/utils/uiPrefs"

import "element-plus/es/components/message/style/css"
import "element-plus/es/components/message-box/style/css"
import "element-plus/es/components/notification/style/css"
// kt.ui widgets import these components directly, so the resolver adds no styles for them.
import "element-plus/es/components/button/style/css"
import "element-plus/es/components/checkbox/style/css"
import "element-plus/es/components/date-picker/style/css"
import "element-plus/es/components/input/style/css"
import "element-plus/es/components/input-number/style/css"
import "element-plus/es/components/option/style/css"
import "element-plus/es/components/radio/style/css"
import "element-plus/es/components/radio-group/style/css"
import "element-plus/es/components/select/style/css"
import "element-plus/es/components/slider/style/css"
import "element-plus/es/components/switch/style/css"
import "element-plus/es/components/tab-pane/style/css"
import "element-plus/es/components/tabs/style/css"
import "uno.css"
import "./style.css"
import "./components/session-v2/surfaces.css"

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
  // Bounded wait: prefs from the backend are nice-to-have at first
  // paint (theme/zoom without flash), but a slow or dead backend
  // must never block app mount. The fetch keeps running past the
  // timeout and fills the cache when it lands.
  await ensureUIPrefsLoaded({ timeoutMs: 2500 })
  initializeAttentionPrefs()

  const pinia = createPinia()
  const overlayKey = overlayKeyFromSearch(location.search)
  const popoutScope = popoutScopeFromSearch(location.search)
  const secondary = Boolean(overlayKey || popoutScope)
  const app = overlayKey
    ? createApp(OverlayWindowApp, { overlayKey })
    : popoutScope
      ? createApp(PopoutApp, { scope: popoutScope })
      : createApp(App)

  app.use(pinia)
  app.use(router)

  registerBuiltinPanels()
  installSharedModules()

  if (secondary) {
    useThemeStore().init()
    useLocaleStore().init()
    installExternalLinkGuard()
  }
  app.mount("#app")
  startUiExtensions()
  if (!secondary) startOverlayWindows()
}

bootstrap()
