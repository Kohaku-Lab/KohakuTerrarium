<template>
  <el-dialog v-model="visible" title="Install package" width="520px" align-center :close-on-click-modal="phase !== 'installing'" :show-close="phase !== 'installing'" @close="onClose">
    <div v-if="pkg" class="flex flex-col gap-4 text-[13px]">
      <!-- Summary -->
      <section class="flex flex-col gap-2">
        <p class="text-warm-700 dark:text-warm-300">This package will run code with your user permissions.</p>

        <dl class="grid grid-cols-[100px_1fr] gap-x-3 gap-y-1 text-[12px]">
          <dt class="text-warm-500 dark:text-warm-400">Package</dt>
          <dd class="font-medium text-warm-700 dark:text-warm-300 break-all">{{ pkg.name }}</dd>

          <dt class="text-warm-500 dark:text-warm-400">Version</dt>
          <dd class="font-mono text-warm-700 dark:text-warm-300">{{ versionLabel }}</dd>

          <dt class="text-warm-500 dark:text-warm-400">Author</dt>
          <dd class="text-warm-700 dark:text-warm-300">{{ pkg.author || "—" }}</dd>

          <dt class="text-warm-500 dark:text-warm-400">License</dt>
          <dd class="text-warm-700 dark:text-warm-300">{{ pkg.license || "—" }}</dd>

          <dt class="text-warm-500 dark:text-warm-400">Framework</dt>
          <dd class="font-mono text-[11px] text-warm-700 dark:text-warm-300">{{ pkg.framework || "any" }}</dd>

          <dt class="text-warm-500 dark:text-warm-400">Source</dt>
          <dd class="text-warm-700 dark:text-warm-300 break-all">
            <a v-if="pkg.repo" :href="pkg.repo" target="_blank" rel="noopener noreferrer" class="text-iolite dark:text-iolite-light hover:underline inline-flex items-center gap-1">
              {{ pkg.repo }}
              <span class="i-carbon-launch text-[10px]" />
            </a>
            <span v-else>—</span>
          </dd>
        </dl>
      </section>

      <!-- Version selector (when multiple non-yanked versions exist) -->
      <section v-if="selectableVersions.length > 1">
        <label class="text-[11px] uppercase tracking-wider text-warm-500 dark:text-warm-400 block mb-1">Version</label>
        <select v-model="selectedTag" class="input-field !text-[12px] cursor-pointer">
          <option v-for="v in selectableVersions" :key="v.tag" :value="v.tag">
            {{ v.tag }}<template v-if="v.released"> — {{ v.released }}</template>
          </option>
        </select>
      </section>

      <!-- Status -->
      <section v-if="phase === 'installing'" class="flex items-center gap-2 py-2 text-warm-500 dark:text-warm-400 text-[12px]">
        <span class="i-carbon-renew text-[14px] kohaku-pulse" />
        <span>Installing {{ pkg.name }}@{{ selectedTag }} …</span>
      </section>

      <section v-if="phase === 'success'" class="flex items-center gap-2 py-2 text-aquamarine text-[12px] font-medium">
        <span class="i-carbon-checkmark-filled text-[14px]" />
        <span>Installed {{ installedName || pkg.name }} successfully.</span>
      </section>

      <section v-if="phase === 'error'" class="flex items-start gap-2 py-2 text-coral text-[12px]">
        <span class="i-carbon-warning-alt-filled text-[14px] shrink-0 mt-0.5" />
        <span class="font-mono break-words">{{ errorMessage }}</span>
      </section>
    </div>

    <template #footer>
      <el-button v-if="phase === 'success'" size="small" @click="onClose">Close</el-button>
      <template v-else>
        <el-button size="small" :disabled="phase === 'installing'" @click="onClose">Cancel</el-button>
        <el-button size="small" type="primary" :disabled="phase === 'installing' || !pkg" :loading="phase === 'installing'" @click="onInstall">
          <span v-if="phase !== 'installing'" class="i-carbon-download text-[12px] mr-1" />
          {{ phase === "error" ? "Try again" : "Install" }}
        </el-button>
      </template>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import { useMarketplaceStore } from "@/stores/marketplace"

const props = defineProps({
  open: { type: Boolean, default: false },
  pkg: { type: Object, default: null },
})
const emit = defineEmits(["close", "installed"])

const marketplace = useMarketplaceStore()

const phase = ref("idle") // idle | installing | success | error
const errorMessage = ref("")
const installedName = ref("")
const selectedTag = ref("")

const visible = computed({
  get: () => props.open,
  set: (v) => {
    if (!v) onClose()
  },
})

const selectableVersions = computed(() => {
  const versions = props.pkg?.versions || []
  // Hide yanked from the selector; users can still type the
  // explicit @name@version via the CLI for reproducibility.
  return versions.filter((v) => !v.yanked)
})

const versionLabel = computed(() => selectedTag.value || (selectableVersions.value[0]?.tag ?? "?"))

watch(
  () => props.pkg,
  (pkg) => {
    phase.value = "idle"
    errorMessage.value = ""
    installedName.value = ""
    selectedTag.value = pkg?.versions?.find((v) => !v.yanked)?.tag || pkg?.versions?.[0]?.tag || ""
  },
  { immediate: true },
)

async function onInstall() {
  if (!props.pkg) return
  phase.value = "installing"
  errorMessage.value = ""
  // Build the spec including the chosen version (when not latest).
  const latest = selectableVersions.value[0]?.tag
  const spec = selectedTag.value && selectedTag.value !== latest ? `@${props.pkg.name}@${selectedTag.value}` : `@${props.pkg.name}`
  try {
    const name = await marketplace.install(spec)
    installedName.value = name
    phase.value = "success"
    emit("installed", { name })
  } catch (err) {
    phase.value = "error"
    errorMessage.value = err?.response?.data?.detail || err?.message || String(err)
  }
}

function onClose() {
  if (phase.value === "installing") return // block close during install
  emit("close")
}
</script>
