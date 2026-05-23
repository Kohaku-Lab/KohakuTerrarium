<template>
  <el-dialog v-model="visible" :title="t('codexLogin.title')" width="520px" align-center :close-on-click-modal="false" :close-on-press-escape="phase !== 'running' || !!userCode" :show-close="phase !== 'running' || !!userCode" @close="onCancel">
    <!-- starting / pre-code state -->
    <div v-if="phase === 'running' && !userCode" class="flex flex-col items-center gap-3 py-6 text-[13px] text-warm-500">
      <span class="i-carbon-renew text-2xl text-amber kohaku-pulse" aria-hidden="true" />
      <span>{{ t("codexLogin.starting") }}</span>
    </div>

    <!-- device-code state — main payload: URL + code -->
    <div v-else-if="userCode" class="flex flex-col gap-4 text-[13px]">
      <!-- Step 1: URL -->
      <section class="flex flex-col gap-1.5">
        <p class="text-warm-700 dark:text-warm-300 font-medium">{{ t("codexLogin.step1Label") }}</p>
        <div class="flex items-center gap-2 rounded-lg bg-iolite/6 dark:bg-iolite/8 border border-iolite/15 dark:border-iolite/20 px-2.5 py-1.5">
          <span class="i-carbon-link text-iolite dark:text-iolite-light shrink-0" aria-hidden="true" />
          <code class="flex-1 font-mono text-[12px] text-warm-700 dark:text-warm-300 truncate select-all">
            {{ deviceUrl }}
          </code>
          <el-button size="small" :title="t('codexLogin.copy')" @click="copyText(deviceUrl, 'url')">
            <span class="i-carbon-copy text-[11px] mr-1" aria-hidden="true" />
            {{ copyState === "url" ? t("codexLogin.copied") : t("codexLogin.copy") }}
          </el-button>
          <el-button size="small" type="primary" plain :title="t('codexLogin.openUrl')" @click="openExternally">
            <span class="i-carbon-launch text-[11px] mr-1" aria-hidden="true" />
            {{ t("codexLogin.openUrl") }}
          </el-button>
        </div>
        <p class="text-[11px] text-warm-400">{{ t("codexLogin.step1Hint") }}</p>
      </section>

      <!-- Step 2: code -->
      <section class="flex flex-col gap-1.5">
        <p class="text-warm-700 dark:text-warm-300 font-medium">{{ t("codexLogin.step2Label") }}</p>
        <div class="flex items-center gap-2 rounded-lg bg-iolite/6 dark:bg-iolite/8 border border-iolite/15 dark:border-iolite/20 px-3 py-2">
          <code class="flex-1 font-mono text-xl tracking-[0.18em] font-bold text-iolite dark:text-iolite-light text-center select-all">
            {{ userCode }}
          </code>
          <el-button size="small" :title="t('codexLogin.copy')" @click="copyText(userCode, 'code')">
            <span class="i-carbon-copy text-[11px] mr-1" aria-hidden="true" />
            {{ copyState === "code" ? t("codexLogin.copied") : t("codexLogin.copy") }}
          </el-button>
        </div>
      </section>

      <!-- Step 3: status -->
      <section class="flex items-center gap-2 text-[12px] text-warm-500">
        <span class="i-carbon-time text-warm-400 shrink-0" aria-hidden="true" />
        <span>{{ t("codexLogin.step3Label") }}</span>
      </section>

      <p v-if="expiresIn > 0" class="text-[11px] text-warm-400 italic">
        {{ t("codexLogin.expiresIn", { minutes: Math.round(expiresIn / 60) }) }}
      </p>

      <p v-if="copyState === 'manual'" class="text-[11px] text-warm-400 italic">
        {{ t("codexLogin.copyManual") }}
      </p>
    </div>

    <!-- success state -->
    <div v-if="phase === 'success'" class="flex items-center gap-2 py-2 text-[13px] text-aquamarine font-medium">
      <span class="i-carbon-checkmark-filled text-lg" aria-hidden="true" />
      <span>{{ t("codexLogin.success") }}</span>
    </div>

    <!-- error state -->
    <div v-if="phase === 'error'" class="flex items-start gap-2 py-2 text-[13px] text-coral">
      <span class="i-carbon-warning-alt-filled text-lg shrink-0 mt-0.5" aria-hidden="true" />
      <span class="font-mono text-[12px] break-words">{{ t("codexLogin.failed", { error: errorMessage }) }}</span>
    </div>

    <template #footer>
      <el-button size="small" @click="onCancel">
        {{ phase === "success" ? t("codexLogin.close") : t("codexLogin.cancel") }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from "vue"

import { useHostsStore } from "@/stores/hosts"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  open: { type: Boolean, default: false },
  node: { type: String, default: "_host" },
})
const emit = defineEmits(["close", "done"])

const hosts = useHostsStore()
const { t } = useI18n()

const deviceUrl = ref("")
const userCode = ref("")
const expiresIn = ref(0)
const phase = ref("idle") // idle | running | success | error
const errorMessage = ref("")
const copyState = ref("") // url / code / manual / ''

let abortController = null

const visible = computed({
  get: () => props.open,
  set: (v) => {
    if (!v) onCancel()
  },
})

function resetState() {
  deviceUrl.value = ""
  userCode.value = ""
  expiresIn.value = 0
  phase.value = "idle"
  errorMessage.value = ""
  copyState.value = ""
}

function buildLoginUrl() {
  const active = hosts.activeHost
  const path = `/api/settings/codex-login-stream${props.node && props.node !== "_host" ? `?node=${encodeURIComponent(props.node)}` : ""}`
  if (active) return `${active.url}${path}`
  return path
}

async function start() {
  resetState()
  phase.value = "running"
  abortController = new AbortController()
  try {
    const active = hosts.activeHost
    const headers = { "Content-Type": "application/json" }
    if (active && active.token) {
      headers.Authorization = `Bearer ${active.token}`
    }
    const resp = await fetch(buildLoginUrl(), {
      method: "POST",
      headers,
      signal: abortController.signal,
    })
    if (!resp.ok) {
      phase.value = "error"
      errorMessage.value = t("codexLogin.httpError", {
        status: resp.status,
        body: await resp.text(),
      })
      return
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder("utf-8")
    let buffer = ""
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let nl
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, nl).trim()
        buffer = buffer.slice(nl + 1)
        if (!line) continue
        let event
        try {
          event = JSON.parse(line)
        } catch (_err) {
          continue
        }
        if (event.event === "device_code") {
          deviceUrl.value = event.verification_url
          userCode.value = event.user_code
          expiresIn.value = event.expires_in || 0
        } else if (event.event === "completed") {
          phase.value = "success"
          emit("done", { expiresAt: event.expires_at })
          setTimeout(() => emit("close"), 1200)
        } else if (event.event === "error") {
          phase.value = "error"
          errorMessage.value = event.message
        }
      }
    }
  } catch (err) {
    if (err.name === "AbortError") return
    phase.value = "error"
    errorMessage.value = err?.message || String(err)
  }
}

function onCancel() {
  if (abortController) abortController.abort()
  abortController = null
  emit("close")
}

async function copyText(value, kind) {
  try {
    await navigator.clipboard.writeText(value)
    copyState.value = kind
    setTimeout(() => {
      if (copyState.value === kind) copyState.value = ""
    }, 1500)
  } catch (_err) {
    // Clipboard API unavailable (some Android WebViews) — let the user
    // long-press the highlighted text instead. ``select-all`` on the
    // code/url makes a triple-tap or long-press one-shot copy.
    copyState.value = "manual"
  }
}

function openExternally() {
  if (!deviceUrl.value) return
  try {
    window.open(deviceUrl.value, "_blank", "noopener,noreferrer")
  } catch (_err) {
    // ignore — fallback path is manual copy + paste
  }
}

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen && phase.value === "idle") start()
    if (!isOpen && abortController) {
      abortController.abort()
      abortController = null
    }
  },
  { immediate: true },
)

onUnmounted(() => {
  if (abortController) abortController.abort()
})
</script>
