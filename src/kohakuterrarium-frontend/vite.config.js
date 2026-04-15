import { defineConfig } from "vite"
import vue from "@vitejs/plugin-vue"
import VueRouter from "unplugin-vue-router/vite"
import AutoImport from "unplugin-auto-import/vite"
import Components from "unplugin-vue-components/vite"
import { VueRouterAutoImports } from "unplugin-vue-router"
import { ElementPlusResolver } from "unplugin-vue-components/resolvers"
import UnoCSS from "unocss/vite"
import { fileURLToPath, URL } from "node:url"

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
  ],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8001",
        changeOrigin: true,
      },
      "/ws/": {
        target: "http://localhost:8001",
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
