<template>
  <!-- kt.ui timer: a local countdown or stopwatch with start, pause and reset. -->
  <div class="ktui-timer" :class="{ 'ktui-timer-done': done }" data-test="ktui-timer">
    <span v-if="label" class="ktui-label">{{ label }}</span>
    <span class="ktui-timer-clock" role="timer" aria-live="off" data-test="ktui-timer-clock">{{ clock }}</span>
    <span v-if="done" class="ktui-badge ktui-tone-warning">{{ t("chat.inlineUi.timeUp") }}</span>
    <el-button size="small" data-test="ktui-timer-toggle" :disabled="done" @click="running ? pause() : start()">{{ running ? t("chat.inlineUi.pause") : t("chat.inlineUi.start") }}</el-button>
    <el-button size="small" link data-test="ktui-timer-reset" @click="reset">{{ t("chat.inlineUi.reset") }}</el-button>
  </div>
</template>

<script setup>
import { getActivePinia } from "pinia"
import { computed, onBeforeUnmount, onMounted, ref } from "vue"
import { ElButton } from "element-plus"

import { translate, useI18n } from "@/utils/i18n"

const TICK_MS = 250

const props = defineProps({
  mode: { type: String, default: "countdown" },
  seconds: { type: Number, default: 0 },
  label: { type: String, default: "" },
  autostart: { type: Boolean, default: false },
  now: { type: Function, default: () => Date.now() },
})

const t = getActivePinia() ? useI18n().t : (key, args) => translate("en", key, args)
const elapsed = ref(0)
const running = ref(false)
let startedAt = 0
let before = 0
let ticker = null

const countdown = computed(() => props.mode !== "stopwatch")
const left = computed(() => Math.max(0, props.seconds * 1000 - elapsed.value))
const done = computed(() => countdown.value && left.value <= 0)
const clock = computed(() => {
  const ms = countdown.value ? left.value : elapsed.value
  const total = countdown.value ? Math.ceil(ms / 1000) : Math.floor(ms / 1000)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  const pad = (n) => String(n).padStart(2, "0")
  return hours ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${pad(minutes)}:${pad(secs)}`
})

function tick() {
  elapsed.value = before + (props.now() - startedAt)
  if (done.value) pause()
}

function start() {
  if (running.value || done.value) return
  startedAt = props.now()
  running.value = true
  ticker = setInterval(tick, TICK_MS)
}

function pause() {
  if (!running.value) return
  elapsed.value = before + (props.now() - startedAt)
  before = elapsed.value
  running.value = false
  clearInterval(ticker)
  ticker = null
}

function reset() {
  pause()
  before = 0
  elapsed.value = 0
}

onMounted(() => {
  if (props.autostart) start()
})
onBeforeUnmount(() => clearInterval(ticker))

defineExpose({ tick })
</script>
