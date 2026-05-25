<template>
  <el-dialog v-model="visible" title="Install from source" width="540px" align-center :close-on-click-modal="phase !== 'installing'" :show-close="phase !== 'installing'" @close="onClose">
    <div class="flex flex-col gap-3 text-[13px]">
      <p class="text-warm-500 dark:text-warm-400 text-[11px]">Install a package from a marketplace spec (<code class="font-mono">@name</code>, <code class="font-mono">@name@v1.2.0</code>, <code class="font-mono">@source/name</code>), a git URL, or a local directory path. Editable installs apply to local paths only.</p>

      <div>
        <label class="block text-[11px] uppercase tracking-wider text-warm-500 dark:text-warm-400 mb-1">Source</label>
        <el-input v-model="source" size="small" placeholder="@kt-biome  •  https://github.com/owner/repo.git  •  /path/to/pack" @keyup.enter="onInstall" />
        <p class="text-[10px] text-warm-500 dark:text-warm-400 mt-1 font-mono">detected: {{ detectedKind }}</p>
      </div>

      <div>
        <label class="block text-[11px] uppercase tracking-wider text-warm-500 dark:text-warm-400 mb-1">Name override (optional)</label>
        <el-input v-model="nameOverride" size="small" placeholder="Leave empty to use the manifest / repo name" />
      </div>

      <div v-if="detectedKind === 'local path'" class="flex items-center gap-2">
        <el-checkbox v-model="editable" />
        <span class="text-[12px] text-warm-700 dark:text-warm-300">Editable install (<code class="font-mono">-e</code>) — write a <code class="font-mono">.link</code> pointer; edits in-place</span>
      </div>

      <section v-if="phase === 'installing'" class="flex items-center gap-2 py-1.5 text-warm-500 dark:text-warm-400 text-[12px]">
        <span class="i-carbon-renew text-[14px] kohaku-pulse" />
        <span>Installing {{ source }} …</span>
      </section>

      <section v-if="phase === 'success'" class="flex items-center gap-2 py-1.5 text-aquamarine text-[12px] font-medium">
        <span class="i-carbon-checkmark-filled text-[14px]" />
        <span>Installed {{ installedName }}.</span>
      </section>

      <section v-if="phase === 'error'" class="flex items-start gap-2 py-1.5 text-coral text-[12px]">
        <span class="i-carbon-warning-alt-filled text-[14px] shrink-0 mt-0.5" />
        <span class="font-mono break-words">{{ errorMessage }}</span>
      </section>
    </div>

    <template #footer>
      <el-button v-if="phase === 'success'" size="small" @click="onClose">Close</el-button>
      <template v-else>
        <el-button size="small" :disabled="phase === 'installing'" @click="onClose">Cancel</el-button>
        <el-button size="small" type="primary" :disabled="phase === 'installing' || !source.trim()" :loading="phase === 'installing'" @click="onInstall">
          <span v-if="phase !== 'installing'" class="i-carbon-download text-[12px] mr-1" />
          {{ phase === "error" ? "Try again" : "Install" }}
        </el-button>
      </template>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import { marketplaceAPI } from "@/utils/marketplaceApi"

const props = defineProps({
  open: { type: Boolean, default: false },
})
const emit = defineEmits(["close", "installed"])

const source = ref("")
const nameOverride = ref("")
const editable = ref(false)
const phase = ref("idle") // idle | installing | success | error
const errorMessage = ref("")
const installedName = ref("")

const visible = computed({
  get: () => props.open,
  set: (v) => {
    if (!v) onClose()
  },
})

const detectedKind = computed(() => {
  const s = source.value.trim()
  if (!s) return "—"
  if (s.startsWith("@")) return "marketplace spec"
  if (/^https?:\/\//i.test(s) || s.endsWith(".git")) return "git URL"
  return "local path"
})

watch(
  () => props.open,
  (v) => {
    if (v) {
      source.value = ""
      nameOverride.value = ""
      editable.value = false
      phase.value = "idle"
      errorMessage.value = ""
      installedName.value = ""
    }
  },
)

async function onInstall() {
  const spec = source.value.trim()
  if (!spec) return
  phase.value = "installing"
  errorMessage.value = ""
  try {
    // ``marketplaceAPI.install`` (POST /api/catalog/marketplace/install)
    // routes through ``install_package_spec`` on the backend, which
    // dispatches by spec shape: @-form → marketplace resolver →
    // ref-pinned clone; URL → git clone; local path → copy or .link.
    // Editable is honored only on local paths (backend rejects
    // editable on marketplace specs with a clear error message).
    const data = await marketplaceAPI.install({
      spec,
      name: nameOverride.value.trim() || null,
      editable: editable.value && detectedKind.value === "local path",
    })
    installedName.value = data.name || spec
    phase.value = "success"
    emit("installed", { name: data.name || spec })
  } catch (err) {
    phase.value = "error"
    errorMessage.value = err?.response?.data?.detail || err?.message || String(err)
  }
}

function onClose() {
  if (phase.value === "installing") return
  emit("close")
}
</script>
