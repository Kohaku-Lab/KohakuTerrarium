<template>
  <div class="h-full min-h-0 flex flex-col pl-2 pt-1" :class="theme.dark ? 'bg-[#1a1a2e]' : 'bg-[#f7f5f2]'">
    <div ref="termEl" class="flex-1 min-h-0 min-w-0" />
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch } from "vue"
import { Terminal } from "@xterm/xterm"
import { FitAddon } from "@xterm/addon-fit"
import { Unicode11Addon } from "@xterm/addon-unicode11"
import { WebLinksAddon } from "@xterm/addon-web-links"
import "@xterm/xterm/css/xterm.css"

import { useThemeStore } from "@/stores/theme"
import { wsUrl } from "@/utils/wsUrl"

/**
 * An xterm bound to one creature's pty WebSocket (`path`, a /ws/... path).
 * Speaks the backend pty protocol: {type: input|resize} out,
 * {type: output|error} in. Reconnects when `path` changes.
 */
const props = defineProps({
  path: { type: String, default: "" },
  disconnectedLabel: { type: String, default: "disconnected" },
})
const emit = defineEmits(["state"])

const DARK = { background: "#1a1a2e", foreground: "#e0e0e0", cursor: "#e0e0e0", selectionBackground: "#44475a" }
const LIGHT = { background: "#f7f5f2", foreground: "#3a3632", cursor: "#3a3632", selectionBackground: "#c8c4be" }
const FONT = "'Consolas NF', 'CaskaydiaCove NF', 'JetBrainsMono NF', 'FiraCode NF', 'JetBrains Mono', 'Fira Code', Consolas, monospace"

const theme = useThemeStore()
const termEl = ref(null)
let term = null
let fit = null
let ws = null
let observer = null
let disposed = false

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg))
}

function connect() {
  if (!props.path || ws || disposed) return
  const socket = new WebSocket(wsUrl(props.path))
  ws = socket
  emit("state", "connecting")
  socket.onopen = () => {
    if (ws !== socket) return
    emit("state", "open")
    if (term) send({ type: "resize", rows: term.rows, cols: term.cols })
  }
  socket.onmessage = (ev) => {
    if (ws !== socket) return
    try {
      const msg = JSON.parse(ev.data)
      if (msg.type === "output") term?.write(msg.data)
      else if (msg.type === "error") term?.write(`\r\n\x1b[31m${msg.data}\x1b[0m\r\n`)
    } catch {
      /* non-JSON frames are ignored */
    }
  }
  socket.onclose = () => {
    if (ws !== socket) return
    ws = null
    emit("state", "closed")
    term?.write(`\r\n\x1b[33m[${props.disconnectedLabel}]\x1b[0m\r\n`)
  }
}

function disconnect() {
  const socket = ws
  if (!socket) return
  ws = null
  try {
    socket.close()
  } catch {
    /* already closing */
  }
  emit("state", "closed")
}

onMounted(async () => {
  term = new Terminal({ allowProposedApi: true, cursorBlink: true, fontSize: 13, fontFamily: FONT, theme: theme.dark ? DARK : LIGHT })
  fit = new FitAddon()
  term.loadAddon(fit)
  term.loadAddon(new Unicode11Addon())
  term.loadAddon(new WebLinksAddon())
  term.unicode.activeVersion = "11"
  if (document.fonts?.ready) await document.fonts.ready
  if (disposed || !termEl.value) return
  term.open(termEl.value)
  fit.fit()
  term.onData((data) => send({ type: "input", data }))
  term.onResize(({ rows, cols }) => send({ type: "resize", rows, cols }))
  if (typeof ResizeObserver !== "undefined") {
    observer = new ResizeObserver(() => fit?.fit())
    observer.observe(termEl.value)
  }
  connect()
})

watch(
  () => theme.dark,
  (dark) => {
    if (term) term.options.theme = dark ? DARK : LIGHT
  },
)
watch(
  () => props.path,
  () => {
    disconnect()
    term?.reset()
    connect()
  },
)

onUnmounted(() => {
  disposed = true
  disconnect()
  observer?.disconnect()
  term?.dispose()
  term = null
})

defineExpose({ connect })
</script>
