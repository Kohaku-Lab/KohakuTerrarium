<template>
  <div class="h-full w-full flex flex-col overflow-hidden" :class="themeStore.dark ? 'bg-[#1a1a2e]' : 'bg-[#f7f5f2]'">
    <!-- Header -->
    <div class="flex items-center gap-2 px-2 h-6 border-b text-[10px] shrink-0" :class="themeStore.dark ? 'bg-warm-900 border-warm-800 text-warm-400' : 'bg-warm-100 border-warm-200 text-warm-500'">
      <span class="i-carbon-terminal text-[11px]" />
      <span>Terminal</span>
      <!-- Terrarium: explicit creature selector so the terminal doesn't
           silently follow the chat tab. Each creature runs in its own
           working directory / environment, so the terminal target must
           be user-visible. -->
      <el-select v-if="isTerrarium" v-model="selectedTarget" size="small" class="terminal-target-select" :placeholder="terminalTargets.length ? 'Select creature' : 'No creatures'" :disabled="!terminalTargets.length">
        <el-option v-for="target in terminalTargets" :key="target" :value="target" :label="target" />
      </el-select>
      <span class="w-1.5 h-1.5 rounded-full" :class="connected ? 'bg-aquamarine' : 'bg-warm-600'" />
      <span class="flex-1" />
      <button v-if="!connected" class="px-1.5 py-0.5 rounded text-warm-500 hover:text-warm-300 hover:bg-warm-800" :disabled="!terminalPath" @click="connect">Connect</button>
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
import { wsUrl } from "@/utils/wsUrl"

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
// Explicit terrarium creature selection. Defaults to the current chat
// tab on mount, but stays independent afterwards so the terminal can
// target a different creature than the one the user is chatting with.
const selectedTarget = ref("")

let term = null
let fitAddon = null
let ws = null
let resizeObserver = null

const sessionId = computed(() => props.instance?.graph_id || props.instance?.id || instances.current?.id || null)
const isTerrarium = computed(() => props.instance?.type === "terrarium")

/**
 * Names of every creature / root the terminal can attach to. The
 * backend's pty endpoint keys on creature name (``root`` for the
 * privileged creature). Solo sessions surface their single creature
 * here too, so the path is uniform across solo and terrarium.
 */
const terminalTargets = computed(() => {
  const inst = props.instance
  const names = []
  if (inst?.has_root) names.push("root")
  for (const c of inst?.creatures || []) {
    if (c?.name && !names.includes(c.name)) names.push(c.name)
  }
  return names
})

const resolvedTarget = computed(() => {
  if (isTerrarium.value) return selectedTarget.value
  return terminalTargets.value[0] || null
})

const terminalPath = computed(() => {
  const sid = sessionId.value
  const target = resolvedTarget.value
  if (!sid || !target) return null
  return `/ws/sessions/${encodeURIComponent(sid)}/creatures/${encodeURIComponent(target)}/pty`
})

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

    // Seed terrarium selection from the current chat tab (so the
    // default matches user expectation) without binding to it — the
    // terminal stays pinned to its own target afterwards.
    if (isTerrarium.value) {
      const chatTarget = chat.terrariumTarget
      if (chatTarget && terminalTargets.value.includes(chatTarget)) {
        selectedTarget.value = chatTarget
      } else if (terminalTargets.value.length) {
        selectedTarget.value = terminalTargets.value[0]
      }
    }

    // Auto-connect if we have a session AND a resolvable path.
    if (sessionId.value && terminalPath.value) connect()
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

// If the available target list changes (creature added/removed, switched
// instance), make sure our selection is still valid. Fall back to the
// first available target.
watch(terminalTargets, (targets) => {
  if (!isTerrarium.value) return
  if (!targets.includes(selectedTarget.value)) {
    selectedTarget.value = targets[0] || ""
  }
})

// Reconnect whenever the target changes — either the instance itself
// (sessionId) or the creature selection (path rebuild).
watch([sessionId, terminalPath], ([sid, path], [prevSid, prevPath]) => {
  if (prevSid || prevPath) disconnect()
  if (sid && path) connect()
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

<style scoped>
.terminal-target-select {
  width: 9rem;
  --el-component-size-small: 20px;
  --el-font-size-small: 10px;
}
.terminal-target-select :deep(.el-input__wrapper) {
  min-height: 18px;
  padding: 0 4px;
  box-shadow: none;
  background: transparent;
}
.terminal-target-select :deep(.el-input__inner) {
  font-size: 10px;
  color: inherit;
  height: 18px;
  line-height: 18px;
}
</style>
