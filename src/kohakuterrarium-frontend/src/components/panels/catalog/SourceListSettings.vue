<template>
  <el-dialog v-model="visible" title="Marketplace sources" width="560px" align-center :close-on-click-modal="true" @close="$emit('close')">
    <div class="flex flex-col gap-4 text-[13px]">
      <p class="text-warm-500 dark:text-warm-400 text-[12px]">Configure which TerrariumMarket-compatible <code class="font-mono">registry.yaml</code> URLs the app consults. Sources are merged in order; the first occurrence of a package name wins (shadowing logged).</p>

      <!-- List -->
      <ul class="flex flex-col gap-1.5">
        <li v-for="src in marketplace.sources" :key="src.url" class="flex items-center gap-2 rounded border border-warm-200 dark:border-warm-700 px-2.5 py-1.5">
          <span class="i-carbon-link text-warm-400 dark:text-warm-500 text-[12px] shrink-0" aria-hidden="true" />
          <div class="flex-1 min-w-0">
            <div class="font-medium text-warm-700 dark:text-warm-300 text-[12px] truncate">{{ src.alias }}</div>
            <div class="font-mono text-[10px] text-warm-500 dark:text-warm-400 truncate">{{ src.url }}</div>
          </div>
          <button v-if="src.alias !== 'default'" type="button" class="btn-icon !w-6 !h-6" :title="`Remove ${src.alias}`" @click="onRemove(src)">
            <span class="i-carbon-trash-can text-[12px]" />
          </button>
        </li>
        <li v-if="!marketplace.sources.length" class="text-warm-400 italic text-[12px] py-2 text-center">No sources configured.</li>
      </ul>

      <!-- Add form -->
      <section class="border-t border-warm-200 dark:border-warm-700 pt-3 flex flex-col gap-2">
        <label class="text-[11px] uppercase tracking-wider text-warm-500 dark:text-warm-400">Add source</label>
        <input v-model="form.url" type="url" class="input-field font-mono" placeholder="https://raw.githubusercontent.com/<owner>/<repo>/main/registry.yaml" />
        <input v-model="form.alias" type="text" class="input-field" :placeholder="`Alias (default: URL)`" />
        <p v-if="errorMessage" class="text-coral text-[12px]">{{ errorMessage }}</p>
        <div class="flex items-center justify-end gap-2">
          <el-button size="small" :disabled="!form.url.trim() || busy" :loading="busy" type="primary" @click="onAdd">
            <span class="i-carbon-add text-[11px] mr-1" />
            Add
          </el-button>
        </div>
      </section>
    </div>

    <template #footer>
      <el-button size="small" @click="$emit('close')">Close</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref } from "vue"
import { ElMessageBox } from "element-plus"

import { useMarketplaceStore } from "@/stores/marketplace"

const props = defineProps({
  open: { type: Boolean, default: false },
})
const emit = defineEmits(["close"])

const marketplace = useMarketplaceStore()

const form = reactive({ url: "", alias: "" })
const errorMessage = ref("")
const busy = ref(false)

const visible = computed({
  get: () => props.open,
  set: (v) => {
    if (!v) emit("close")
  },
})

async function onAdd() {
  busy.value = true
  errorMessage.value = ""
  try {
    await marketplace.addSource({ url: form.url.trim(), alias: form.alias.trim() || null })
    form.url = ""
    form.alias = ""
  } catch (err) {
    errorMessage.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    busy.value = false
  }
}

async function onRemove(src) {
  try {
    await ElMessageBox.confirm(`Remove source ${src.alias}?`, "Confirm", {
      type: "warning",
      confirmButtonText: "Remove",
      cancelButtonText: "Cancel",
    })
  } catch {
    return
  }
  try {
    await marketplace.removeSource(src.alias)
  } catch (err) {
    errorMessage.value = err?.response?.data?.detail || err?.message || String(err)
  }
}
</script>
