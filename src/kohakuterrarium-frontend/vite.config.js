import { defineConfig } from "vite"
import vue from "@vitejs/plugin-vue"
import VueRouter from "unplugin-vue-router/vite"
import AutoImport from "unplugin-auto-import/vite"
import Components from "unplugin-vue-components/vite"
import { VueRouterAutoImports } from "unplugin-vue-router"
import { ElementPlusResolver } from "unplugin-vue-components/resolvers"
import UnoCSS from "unocss/vite"
import { fileURLToPath, URL } from "node:url"

import extSharedPlugin from "./scripts/vite-ext-shared.mjs"

// Backend the dev server proxies to; set KT_API_TARGET when the API runs on another port.
const apiTarget = process.env.KT_API_TARGET || "http://127.0.0.1:8001"

export default defineConfig({
  plugins: [
    VueRouter({
      routesFolder: "src/pages",
    }),
    vue(),
    UnoCSS(),
    AutoImport({
      imports: ["vue", "pinia", VueRouterAutoImports],
      resolvers: [ElementPlusResolver()],
    }),
    Components({
      dirs: ["src/components"],
      resolvers: [ElementPlusResolver()],
    }),
    extSharedPlugin(),
  ],
  resolve: {
    alias: [
      {
        find: /^@kohakuterrarium\/chat-ui$/,
        replacement: fileURLToPath(new URL("./src/public/chat/index.js", import.meta.url)),
      },
      { find: "@", replacement: fileURLToPath(new URL("./src", import.meta.url)) },
    ],
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/ws/": {
        target: apiTarget,
        ws: true,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "../kohakuterrarium/web_dist",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          // The template compiler for UI extensions stays in its own lazy chunk.
          if (id.includes("vue/dist/vue.esm-bundler")) return undefined
          if (id.includes("highlight.js")) return "highlight"
          if (id.includes("element-plus")) return "element-plus"
          if (id.includes("vue-flow")) return "vue-flow"
          if (id.includes("monaco-editor")) return "monaco-editor"
          if (
            id.includes("node_modules/vue") ||
            id.includes("node_modules/pinia") ||
            id.includes("node_modules/vue-router")
          )
            return "vue-vendor"
        },
      },
    },
  },
})
