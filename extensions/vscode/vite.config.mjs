import path from 'node:path'
import { fileURLToPath } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'
import UnoCSS from 'unocss/vite'
import AutoImport from 'unplugin-auto-import/vite'

const here = path.dirname(fileURLToPath(import.meta.url))
const frontendRoot = path.resolve(here, '../../src/kohakuterrarium-frontend')
const frontend = path.resolve(frontendRoot, 'src')
const webview = path.resolve(here, 'src/webview')
// The Extension's OWN installed packages. The shared production UIEventBlock
// renders real Element Plus widgets and Vue, so the Extension declares those
// dependencies directly (see package.json) instead of reaching into the
// Dashboard's node_modules. Element Plus (and its whole runtime closure) is
// installed here, so there is no hand-maintained alias list of arbitrary
// transitive packages: Node resolves each dependency from the package that
// imports it.
const selfModules = path.join(here, 'node_modules')

// Exact-match so ``@/utils/i18n/locales`` falls through to the generic ``@``
// alias and resolves the real dictionary tables instead of the local shim.
const i18nModule = new RegExp('^@/utils/i18n$')

// One coherent element-plus scope: every subpath (component CSS, the real
// package entry the shim re-exports) resolves from the Extension's own copy.
const elementPlusPackage = {
  find: /^element-plus\/(.*)$/,
  replacement: path.join(selfModules, 'element-plus') + '/$1',
}
// One coherent Vue closure: the bare ``vue`` root, its subpaths, and the whole
// ``@vue/*`` scope all resolve to the SINGLE installed copy in the Extension.
// Element Plus' peer dependency on ``vue`` therefore lands on the same
// runtime-core/runtime-dom/reactivity the webview itself uses (no split
// 3.5.x/3.5.y closure).
const vueScope = {
  find: /^@vue\/(.*)$/,
  replacement: path.join(selfModules, '@vue') + '/$1',
}

export default defineConfig({
  root: webview,
  define: {
    'process.env.NODE_ENV': JSON.stringify('production'),
    __VUE_OPTIONS_API__: true,
    __VUE_PROD_DEVTOOLS__: false,
    __VUE_PROD_HYDRATION_MISMATCH_DETAILS__: false,
  },
  plugins: [
    vue(),
    // Reuse the Dashboard's real utility + Carbon icon atoms so shared
    // components (CommandResultMessage, ConversationMessage, UIEventBlock) keep
    // their production styling instead of a second hand-rolled stylesheet.
    UnoCSS({ configFile: path.join(frontendRoot, 'uno.config.js') }),
    AutoImport({ imports: ['vue', 'pinia'] }),
  ],
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
      // Exact-match the bare ``vue``/``pinia`` roots so ``vue/jsx-runtime`` and
      // package subpaths still resolve through Node instead of being mangled.
      {
        find: /^vue$/,
        replacement: path.join(here, 'node_modules/vue/dist/vue.runtime.esm-bundler.js'),
      },
      {
        find: /^pinia$/,
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
      // The bare ``element-plus`` root resolves to the host integration shim
      // (real components + the webview notification surface for ElMessage); any
      // subpath (component CSS, the real package entry) resolves straight from
      // the Extension's installed package.
      {
        find: /^element-plus$/,
        replacement: path.join(webview, 'shims/element.js'),
      },
      elementPlusPackage,
      vueScope,
      {
        find: '@/stores/cluster',
        replacement: path.join(webview, 'shims/stores.js'),
      },
      {
        find: '@/stores/instances',
        replacement: path.join(webview, 'shims/stores.js'),
      },
      {
        // Locale selection authority: reuse the Dashboard store so the webview
        // shares one supported-locale list and dictionary provider. Only its
        // preference source (the host language) is supplied by the shim below.
        find: '@/stores/locale',
        replacement: path.join(frontend, 'stores/locale.js'),
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
        // Shared i18n provider seam: resolve the production module so shared
        // components localize through the real dictionary tables in both hosts.
        find: i18nModule,
        replacement: path.join(frontend, 'utils/i18n.js'),
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
        replacement: path.join(webview, 'shims/visibility.mjs'),
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
