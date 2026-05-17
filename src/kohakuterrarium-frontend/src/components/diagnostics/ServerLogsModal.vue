<template>
  <el-dialog v-model="visible" :title="t('serverLogs.title')" width="820px" :close-on-click-modal="false" @close="onClose">
    <div class="flex flex-col gap-2 h-[60vh]">
      <div class="flex items-center gap-3 text-[12px]">
        <el-checkbox v-model="follow" @change="reconnect">{{ t("serverLogs.follow") }}</el-checkbox>
        <span>{{ t("serverLogs.level") }}</span>
        <el-select v-model="level" size="small" style="width: 110px" @change="reconnect">
          <el-option value="DEBUG" label="DEBUG" />
          <el-option value="INFO" label="INFO" />
          <el-option value="WARNING" label="WARNING" />
          <el-option value="ERROR" label="ERROR" />
        </el-select>
        <span>{{ t("serverLogs.lines") }}</span>
        <el-input-number v-model="lines" :min="50" :max="5000" size="small" :controls="false" style="width: 100px" @change="reconnect" />
        <span class="ml-auto text-warm-400 text-[11px]" :class="connected ? 'text-iolite' : 'text-amber-shadow dark:text-amber-light'">
          {{ connected ? t("serverLogs.connected") : t("serverLogs.disconnected") }}
        </span>
      </div>

      <div ref="scrollEl" class="flex-1 min-h-0 overflow-y-auto rounded border border-warm-200 dark:border-warm-700 bg-warm-50 dark:bg-warm-900 p-2 font-mono text-[11px] leading-tight whitespace-pre-wrap">
        <div v-for="(line, idx) in entries" :key="idx" :class="lineClass(line)">{{ line }}</div>
        <div v-if="!entries.length" class="text-warm-400">{{ t("serverLogs.empty") }}</div>
      </div>

      <div class="flex gap-2">
        <el-button size="small" plain @click="clear">
          <span class="i-carbon-trash-can mr-1" />
          {{ t("serverLogs.clear") }}
        </el-button>
        <el-button size="small" plain @click="download">
          <span class="i-carbon-download mr-1" />
          {{ t("serverLogs.download") }}
        </el-button>
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue"

import { useI18n } from "@/utils/i18n"

const props = defineProps({ modelValue: { type: Boolean, default: false } })
const emit = defineEmits(["update:modelValue"])
const { t } = useI18n()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit("update:modelValue", v),
})

const entries = ref([])
const follow = ref(true)
const level = ref("INFO")
const lines = ref(500)
const connected = ref(false)
const scrollEl = ref(null)
let ws = null

function lineClass(line) {
  if (/\[ERROR\]/.test(line)) return "text-coral"
  if (/\[WARN(ING)?\]/.test(line)) return "text-amber-shadow dark:text-amber-light"
  if (/\[DEBUG\]/.test(line)) return "text-warm-400"
  return "text-warm-700 dark:text-warm-300"
}

function scrollToBottom() {
  nextTick(() => {
    const el = scrollEl.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function buildUrl() {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:"
  const params = new URLSearchParams({
    follow: follow.value ? "true" : "false",
    level: level.value,
    lines: String(lines.value),
  })
  return `${proto}//${window.location.host}/ws/daemon/logs?${params.toString()}`
}

function close() {
  if (ws) {
    try {
      ws.close()
    } catch {
      // already closed
    }
    ws = null
  }
  connected.value = false
}

function reconnect() {
  close()
  if (!visible.value) return
  entries.value = []
  ws = new WebSocket(buildUrl())
  ws.onopen = () => {
    connected.value = true
  }
  ws.onmessage = (e) => {
    try {
      const frame = JSON.parse(e.data)
      if (frame.line != null) {
        entries.value.push(frame.line)
        if (entries.value.length > 5000) entries.value.splice(0, entries.value.length - 5000)
        if (follow.value) scrollToBottom()
      }
    } catch {
      // ignore malformed frame
    }
  }
  ws.onclose = () => {
    connected.value = false
  }
}

function clear() {
  entries.value = []
}

function download() {
  const blob = new Blob([entries.value.join("\n")], { type: "text/plain;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = `web.log`
  a.click()
  URL.revokeObjectURL(url)
}

function onClose() {
  close()
}

watch(visible, (v) => {
  if (v) reconnect()
  else close()
})
</script>
