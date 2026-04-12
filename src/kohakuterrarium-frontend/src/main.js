import { createApp } from "vue"
import { createPinia } from "pinia"
import { createRouter, createWebHistory } from "vue-router"
import { routes } from "vue-router/auto-routes"
import App from "./App.vue"

import { registerBuiltinPanels } from "@/stores/layoutPanels"

import "element-plus/es/components/message/style/css"
import "element-plus/es/components/message-box/style/css"
import "element-plus/es/components/notification/style/css"
import "uno.css"
import "./style.css"

const router = createRouter({
  history: createWebHistory(),
  routes,
})

const pinia = createPinia()
const app = createApp(App)

app.use(pinia)
app.use(router)

// Register the canonical panel definitions + builtin presets in the layout
// store. MUST run synchronously after `app.use(pinia)` — if this were async
// the route component would mount before presets exist and `switchPreset()`
// would silently fail.
registerBuiltinPanels()

app.mount("#app")
