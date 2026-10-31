<template>
  <div v-if="hidden" class="hidden" data-test="ext-owner-absent" :data-ext="key" />
  <div v-else class="kt-ext-contribution" :class="inline ? 'min-w-0' : 'h-full w-full overflow-auto'" :data-ext="key">
    <div v-if="contribution.isolation === 'frame'" class="kt-ext-notice" data-test="ext-frame-unsupported">
      <span class="i-carbon-shield" />
      <div>
        <div class="font-medium">{{ contribution.label }}</div>
        <div class="text-[11px]">Isolated (frame) extensions are not supported yet. This contribution is reserved and shows no content.</div>
      </div>
    </div>
    <div v-else-if="failure" class="kt-ext-notice !text-coral" data-test="ext-error">
      <span class="i-carbon-warning-alt" />
      <div class="min-w-0">
        <div class="font-medium">{{ contribution.label }} ({{ pkg }}) failed</div>
        <pre class="text-[11px] whitespace-pre-wrap break-words">{{ failure }}</pre>
        <el-button size="small" @click="retry">Retry</el-button>
      </div>
    </div>
    <div v-else-if="!component || waitingForOwner" class="kt-ext-notice" data-test="ext-loading">
      <span class="i-carbon-renew kohaku-pulse" />
      <span>Loading {{ contribution.label }}…</span>
    </div>
    <Suspense v-else>
      <component :is="component" v-bind="$attrs" />
    </Suspense>
  </div>
</template>

<script setup>
import { computed, onErrorCaptured, onUnmounted, provide, ref, watch } from "vue"

import { createOwnerClient } from "@/ext/ownerClient"
import { useOwnerScope } from "@/ext/ownerScope"
import { EXTENSION_INJECT_KEY, OWNER_INJECT_KEY } from "@/ext/sdk/keys"
import { contributionComponents, contributionKey, findPackage, runtime } from "@/ext/state"

defineOptions({ inheritAttrs: false })

const props = defineProps({
  pkg: { type: String, required: true },
  contribution: { type: Object, required: true },
})

const key = computed(() => contributionKey(props.pkg, props.contribution.id))
const component = computed(() => contributionComponents.get(key.value) || null)
const renderError = ref("")
const loadError = ref("")
const failure = computed(() => renderError.value || loadError.value)
const INLINE_KINDS = ["tool-renderer", "trigger-renderer", "message-renderer"]
const inline = computed(() => INLINE_KINDS.includes(props.contribution.kind))

const owner = createOwnerClient(props.contribution.owner || `package:${props.pkg}`, useOwnerScope())
provide(OWNER_INJECT_KEY, owner)

// A plugin's side panel exists only while the focused creature has that plugin loaded.
const ownerGated = props.contribution.kind === "panel"
if (ownerGated) owner.start()
const hidden = computed(() => ownerGated && owner.status.value === "absent")
const waitingForOwner = computed(() => ownerGated && owner.status.value !== "ready" && owner.status.value !== "error")

// Forwards to the package's ``kt`` handle, which exists once its entry has loaded.
const handleFacade = new Proxy(
  {},
  {
    get(_target, prop) {
      const handle = findPackage(props.pkg)?.handle
      if (!handle) throw new Error(`${props.pkg}: extension handle used before its entry loaded`)
      return handle[prop]
    },
  },
)
provide(EXTENSION_INJECT_KEY, handleFacade)

async function load() {
  loadError.value = ""
  if (props.contribution.isolation === "frame") return
  try {
    await runtime.ensureLoaded(props.pkg)
    if (!contributionComponents.get(key.value)) {
      loadError.value = `The package loaded but did not register "${props.contribution.id}".`
    }
  } catch (err) {
    loadError.value = err?.message || String(err)
  }
}

function retry() {
  renderError.value = ""
  load()
  if (ownerGated) owner.refresh()
}

onErrorCaptured((err) => {
  renderError.value = err?.stack || err?.message || String(err)
  return false
})

onUnmounted(() => owner.dispose())

watch(key, load, { immediate: true })
</script>

<style scoped>
.kt-ext-notice {
  display: flex;
  gap: 0.5rem;
  align-items: flex-start;
  padding: 1rem;
  font-size: 12px;
  color: var(--color-text-muted);
}
</style>
