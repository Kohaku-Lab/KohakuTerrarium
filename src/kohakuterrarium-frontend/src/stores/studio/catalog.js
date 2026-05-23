import { defineStore } from "pinia"
import { computed, ref } from "vue"

import { catalogAPI } from "@/utils/studio/api"

/**
 * Studio catalog — cached lists of what the core framework and
 * installed / workspace kt packages expose for creatures to wire in:
 * tools, sub-agents, triggers, plugins, inputs, outputs, models,
 * plugin hooks. Read-only; refreshed on demand.
 */
export const useStudioCatalogStore = defineStore("studio-catalog", () => {
  const tools = ref([])
  const subagents = ref([])
  const triggers = ref([])
  const plugins = ref([])
  const inputs = ref([])
  const outputs = ref([])
  const models = ref([])
  const pluginHooks = ref([])

  // The previous version stashed a ``loaded`` flag and short-
  // circuited on subsequent calls (``if (loaded.value && !force)
  // return``).  No caller ever passed ``force: true``, so the
  // catalog froze on first load — a user who ran ``kt install``
  // mid-session never saw the new package's tools / sub-agents /
  // plugins / triggers / models / hooks until a full page reload.
  // The catalog endpoints are read-only directory scans on the
  // backend; refetching on every editor open is the right shape.
  const loading = ref(false)
  const loaded = ref(false) // kept for backwards-compat with any
  // consumer reading ``catalog.loaded``;
  // flips true after the first successful
  // fetch and stays true (data is always
  // fresh because we always fetch).
  const error = ref(null)

  async function fetchAll() {
    if (loading.value) return
    loading.value = true
    error.value = null
    try {
      const [t, s, tr, pl, inp, out, m, h] = await Promise.all([
        catalogAPI.tools(),
        catalogAPI.subagents(),
        catalogAPI.triggers(),
        catalogAPI.plugins(),
        catalogAPI.inputs(),
        catalogAPI.outputs(),
        catalogAPI.models(),
        catalogAPI.pluginHooks(),
      ])
      tools.value = t
      subagents.value = s
      triggers.value = tr
      plugins.value = pl
      inputs.value = inp
      outputs.value = out
      models.value = m
      pluginHooks.value = h
      loaded.value = true
    } catch (e) {
      error.value = e
    } finally {
      loading.value = false
    }
  }

  function byName(list) {
    const m = new Map()
    for (const item of list) m.set(item.name, item)
    return (name) => m.get(name) || null
  }

  const toolByName = computed(() => byName(tools.value))
  const subagentByName = computed(() => byName(subagents.value))
  const triggerByName = computed(() => byName(triggers.value))
  const pluginByName = computed(() => byName(plugins.value))
  const inputByName = computed(() => byName(inputs.value))
  const outputByName = computed(() => byName(outputs.value))
  const hookByName = computed(() => byName(pluginHooks.value))

  return {
    tools,
    subagents,
    triggers,
    plugins,
    inputs,
    outputs,
    models,
    pluginHooks,
    loaded,
    loading,
    error,
    fetchAll,
    toolByName,
    subagentByName,
    triggerByName,
    pluginByName,
    inputByName,
    outputByName,
    hookByName,
  }
})
