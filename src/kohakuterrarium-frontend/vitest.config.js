import { defineConfig } from "vitest/config"
import vue from "@vitejs/plugin-vue"
import AutoImport from "unplugin-auto-import/vite"
import { fileURLToPath, URL } from "node:url"

// Minimal vitest config — shares a subset of vite's plugins so .vue
// files and pinia/vue auto-imports work in tests.
export default defineConfig({
  plugins: [
    vue(),
    AutoImport({
      imports: ["vue", "pinia"],
      dts: false,
    }),
  ],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    environmentOptions: {
      jsdom: {
        // Don't try to fetch <img> / <link> assets; they have no
        // bundler context in tests so any reference to /foo.png throws
        // a URL parse error.
        resources: "usable",
        url: "http://localhost/",
      },
    },
    globals: false,
    include: ["src/**/*.test.js", "src/**/*.test.ts"],
    // jsdom is flagged unstable for form elements — suppress the noise.
    silent: false,
  },
})
