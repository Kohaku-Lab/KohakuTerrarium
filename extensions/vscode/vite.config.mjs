import path from 'node:path'
import { fileURLToPath } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'
import AutoImport from 'unplugin-auto-import/vite'

const here = path.dirname(fileURLToPath(import.meta.url))
const frontend = path.resolve(here, '../../src/kohakuterrarium-frontend/src')
const webview = path.resolve(here, 'src/webview')

export default defineConfig({
  root: webview,
  define: {
    'process.env.NODE_ENV': JSON.stringify('production'),
    __VUE_OPTIONS_API__: true,
    __VUE_PROD_DEVTOOLS__: false,
    __VUE_PROD_HYDRATION_MISMATCH_DETAILS__: false,
  },
  plugins: [vue(), AutoImport({ imports: ['vue', 'pinia'] })],
  resolve: {
    dedupe: ['vue', 'pinia'],
    alias: [
      {
        find: /^markdown-it$/,
        replacement: fileURLToPath(import.meta.resolve('markdown-it')),
      },
      {
        find: /^@vscode\/markdown-it-katex$/,
        replacement: fileURLToPath(import.meta.resolve('@vscode/markdown-it-katex')),
      },
      {
        find: /^highlight\.js$/,
        replacement: fileURLToPath(import.meta.resolve('highlight.js')),
      },
      {
        find: /^katex\/dist\/katex\.min\.css$/,
        replacement: fileURLToPath(import.meta.resolve('katex/dist/katex.min.css')),
      },
      {
        find: 'vue',
        replacement: path.join(here, 'node_modules/vue/dist/vue.runtime.esm-bundler.js'),
      },
      {
        find: 'pinia',
        replacement: path.join(here, 'node_modules/pinia/dist/pinia.mjs'),
      },
      {
        find: /^@kohakuterrarium\/chat-ui$/,
        replacement: path.join(frontend, 'public/chat/index.js'),
      },
      {
        find: '@/stores/chat',
        replacement: path.join(frontend, 'stores/chat.js'),
      },
      {
        find: 'element-plus',
        replacement: path.join(webview, 'shims/element.js'),
      },
      {
        find: '@/stores/cluster',
        replacement: path.join(webview, 'shims/stores.js'),
      },
      {
        find: '@/stores/instances',
        replacement: path.join(webview, 'shims/stores.js'),
      },
      {
        find: '@/stores/locale',
        replacement: path.join(webview, 'shims/stores.js'),
      },
      {
        find: '@/stores/messages',
        replacement: path.join(webview, 'shims/stores.js'),
      },
      {
        find: '@/stores/notifications',
        replacement: path.join(webview, 'shims/stores.js'),
      },
      {
        find: '@/stores/status',
        replacement: path.join(webview, 'shims/stores.js'),
      },
      { find: '@/utils/api', replacement: path.join(webview, 'shims/api.js') },
      {
        find: '@/utils/i18n',
        replacement: path.join(webview, 'shims/misc.js'),
      },
      {
        find: '@/utils/uiPrefs',
        replacement: path.join(webview, 'shims/misc.js'),
      },
      {
        find: '@/utils/wsUrl',
        replacement: path.join(webview, 'shims/misc.js'),
      },
      {
        find: '@/composables/useVisibilityInterval',
        replacement: path.join(webview, 'shims/visibility.js'),
      },
      { find: '@', replacement: frontend },
    ],
  },
  build: {
    outDir: path.join(here, 'dist'),
    emptyOutDir: false,
    lib: {
      entry: path.join(webview, 'index.js'),
      formats: ['iife'],
      name: 'KohakuTerrariumVsCode',
      fileName: () => 'webview.js',
      cssFileName: 'webview',
    },
    sourcemap: true,
  },
})
