<template>
  <div class="h-full flex flex-col overflow-hidden">
    <InspectorHeader :target="target" :instance="instance" />

    <!-- Inner-tab strip -->
    <div class="h-9 flex items-center px-2 border-b border-warm-200 dark:border-warm-700 bg-warm-100 dark:bg-warm-900">
      <button v-for="entry in innerTabsList" :key="entry.id" class="h-9 px-3 text-xs" :class="activeInner === entry.id ? 'border-b-2 border-iolite text-warm-800 dark:text-warm-200 -mb-px' : 'text-warm-500 hover:text-warm-700 dark:hover:text-warm-300'" @click="setInner(entry.id)">
        {{ entry.label }}
      </button>
    </div>

    <!-- Active inner -->
    <component :is="ActiveInner" v-if="ActiveInner" :target="target" :instance="instance" class="flex-1 overflow-hidden" />
    <div v-else class="flex-1 p-4 text-warm-400 italic">No inner tab selected.</div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue"

import InspectorHeader from "@/components/shell/tabs/InspectorHeader.vue"
import { useInstancesStore } from "@/stores/instances"
import { inspectorInnerTabs } from "@/stores/tabKindRegistry"

const props = defineProps({ tab: { type: Object, required: true } })

const target = computed(() => props.tab.target)
const instances = useInstancesStore()
const instance = computed(() => instances.list.find((i) => i.id === target.value) ?? null)

const STORAGE_KEY = computed(() => `kt.inspect.${target.value}.inner`)

const activeInner = ref(loadInner())

function loadInner() {
  try {
    return localStorage.getItem(STORAGE_KEY.value) ?? "overview"
  } catch {
    return "overview"
  }
}

function setInner(id) {
  activeInner.value = id
  try {
    localStorage.setItem(STORAGE_KEY.value, id)
  } catch {
    /* swallow */
  }
}

const innerTabsList = computed(() => [...inspectorInnerTabs.entries()].map(([id, def]) => ({ id, ...def })).sort((a, b) => a.order - b.order))

const ActiveInner = computed(() => inspectorInnerTabs.get(activeInner.value)?.component ?? null)

// Lazy fetch the instance if not yet in the list (the rail's polling
// fills it in for running targets, but a freshly-mounted inspector
// may beat the poll).
onMounted(async () => {
  if (!instance.value && typeof instances.fetchOne === "function") {
    try {
      await instances.fetchOne(target.value)
    } catch {
      /* swallow — instance may not exist; UI degrades gracefully */
    }
  }
})

// If the user ever navigates to a different target, reset inner-tab to
// the persisted value for that target.
watch(target, () => {
  activeInner.value = loadInner()
})
</script>
