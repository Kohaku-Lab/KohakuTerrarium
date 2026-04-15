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
