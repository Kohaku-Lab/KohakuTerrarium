<template>
  <div class="h-full w-full flex flex-col overflow-hidden" :class="themeStore.dark ? 'bg-[#1a1a2e]' : 'bg-[#f7f5f2]'">
    <!-- Header -->
    <div class="flex items-center gap-2 px-2 h-6 border-b text-[10px] shrink-0" :class="themeStore.dark ? 'bg-warm-900 border-warm-800 text-warm-400' : 'bg-warm-100 border-warm-200 text-warm-500'">
      <span class="i-carbon-terminal text-[11px]" />
      <span>Terminal</span>
      <span class="w-1.5 h-1.5 rounded-full" :class="connected ? 'bg-aquamarine' : 'bg-warm-600'" />
      <span class="flex-1" />
      <button v-if="!connected" class="px-1.5 py-0.5 rounded text-warm-500 hover:text-warm-300 hover:bg-warm-800" @click="connect">Connect</button>
    </div>
    <!-- Terminal container -->
    <div ref="termEl" class="flex-1 min-h-0" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue"
import { Terminal } from "@xterm/xterm"
import { FitAddon } from "@xterm/addon-fit"
import { Unicode11Addon } from "@xterm/addon-unicode11"
import { WebLinksAddon } from "@xterm/addon-web-links"
import { WebglAddon } from "@xterm/addon-webgl"
import "@xterm/xterm/css/xterm.css"

import { useChatStore } from "@/stores/chat"
import { useInstancesStore } from "@/stores/instances"
import { useThemeStore } from "@/stores/theme"

const props = defineProps({
  instance: { type: Object, default: null },
})

const chat = useChatStore()
const instances = useInstancesStore()
const themeStore = useThemeStore()

const DARK_THEME = {
  background: "#1a1a2e",
  foreground: "#e0e0e0",
  cursor: "#e0e0e0",
  selectionBackground: "#44475a",
}

const LIGHT_THEME = {
  background: "#f7f5f2",
  foreground: "#3a3632",
  cursor: "#3a3632",
  selectionBackground: "#c8c4be",
}
const termEl = ref(null)
const connected = ref(false)

let term = null
let fitAddon = null
let ws = null
let resizeObserver = null

const agentId = computed(() => props.instance?.id || instances.current?.id || null)
const terminalPath = computed(() => {
  const id = agentId.value
  if (!id) return null
  if (props.instance?.type === "terrarium") {
    const target = chat.terrariumTarget
    if (!target) return null
    return `/ws/terminal/terrariums/${id}/${encodeURIComponent(target)}`
  }
  return `/ws/terminal/${id}`
})

function wsUrl(path) {
  if (typeof window === "undefined") return path
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:"
  return `${scheme}//${window.location.host}${path}`
}

let unmounted = false

function connect() {
  if (!terminalPath.value || ws || unmounted) return
  ws = new WebSocket(wsUrl(terminalPath.value))

  ws.onopen = () => {
    connected.value = true
    // Send initial resize.
    if (term) {
      ws.send(
        JSON.stringify({
          type: "resize",
          rows: term.rows,
          cols: term.cols,
        }),
      )
    }
  }

  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data)
      if (msg.type === "output" && term) {
        term.write(msg.data)
      } else if (msg.type === "error" && term) {
        term.write("\r\n\x1b[31m" + msg.data + "\x1b[0m\r\n")
      }
    } catch {
      // ignore
    }
  }

  ws.onclose = (ev) => {
    console.warn("[TerminalPanel] WebSocket closed", ev.code, ev.reason, ev.wasClean)
    connected.value = false
    ws = null
    if (term) term.write("\r\n\x1b[33m[disconnected]\x1b[0m\r\n")
  }

  ws.onerror = (ev) => {
    console.error("[TerminalPanel] WebSocket error", ev)
    connected.value = false
  }
}

function disconnect() {
  if (ws) {
    try {
      ws.close()
    } catch {
      /* ignore */
    }
    ws = null
  }
  connected.value = false
}

onMounted(async () => {
  try {
    term = new Terminal({
      allowProposedApi: true, // required for Unicode11Addon
      cursorBlink: true,
      fontSize: 13,
      fontFamily: "'Consolas NF', 'CaskaydiaCove NF', 'CaskaydiaCove Nerd Font', 'JetBrainsMono NF', 'FiraCode NF', 'Hack NF', 'JetBrains Mono', 'Fira Code', Consolas, monospace",
      theme: themeStore.dark ? DARK_THEME : LIGHT_THEME,
    })

    fitAddon = new FitAddon()
    const unicode11 = new Unicode11Addon()
    term.loadAddon(fitAddon)
    term.loadAddon(unicode11)
    term.loadAddon(new WebLinksAddon())
    // Activate Unicode11 so Nerd Font glyphs (2-cell wide) are measured correctly.
    // Without this, box-drawing chars and icons render misaligned.
    term.unicode.activeVersion = "11"

    if (termEl.value) {
      // Wait for fonts to load so xterm.js measures glyphs correctly.
      if (document.fonts?.ready) {
        await document.fonts.ready
      }
      term.open(termEl.value)
      // WebGL renderer handles font fallback (Nerd Font glyphs) much better
      // than the default canvas renderer. Fall back silently if GPU unavailable.
      try {
        term.loadAddon(new WebglAddon())
      } catch {
        // WebGL not available — canvas renderer is fine
      }
      fitAddon.fit()
    }

    // Forward keystrokes to WS.
    term.onData((data) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "input", data }))
      }
    })

    // Handle resize.
    term.onResize(({ rows, cols }) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resize", rows, cols }))
      }
    })

    // Watch container resize to refit.
    if (termEl.value && typeof ResizeObserver !== "undefined") {
      resizeObserver = new ResizeObserver(() => {
        fitAddon?.fit()
      })
      resizeObserver.observe(termEl.value)
    }

    // Auto-connect if we have an agent.
    if (agentId.value) connect()
  } catch (err) {
    console.error("[TerminalPanel] onMounted error:", err)
  }
})

// React to theme toggle.
watch(
  () => themeStore.dark,
  (dark) => {
    if (term) {
      term.options.theme = dark ? DARK_THEME : LIGHT_THEME
    }
  },
)

watch([agentId, terminalPath], ([id], [prevId]) => {
  if (prevId) disconnect()
  if (id) connect()
})

onUnmounted(() => {
  unmounted = true
  disconnect()
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  if (term) {
    term.dispose()
    term = null
  }
})
</script>
