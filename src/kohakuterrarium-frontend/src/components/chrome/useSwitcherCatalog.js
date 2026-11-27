import { computed, ref, shallowRef, watch } from "vue"

import {
  bindProviderPresets,
  loadProviderModels,
  providerModelsEntry,
} from "../../utils/providerModels.js"

// Discovery is an optional host capability. Only the provider being viewed
// contacts its upstream, and only an explicit save can create a preset.
// Catalogs come from the shared provider-models cache: cached rows show at
// once and a stale list revalidates in the background.
export function useSwitcherCatalog(api, open, provider, presets) {
  const providers = ref([])
  const entry = shallowRef(null)
  const rows = computed(() =>
    bindProviderPresets(entry.value?.rows.value || [], presets.value, provider.value),
  )
  const busy = computed(() => Boolean(entry.value?.loading.value && !entry.value.hasLoaded.value))
  const error = computed(() =>
    entry.value?.failed.value
      ? entry.value.errorDetail.value || "Could not load provider models."
      : "",
  )
  const fetchedAt = computed(() => entry.value?.fetchedAt.value || "")
  const providersError = ref("")
  const sourceOverride = ref(null)
  const showSaved = computed({
    get: () =>
      sourceOverride.value ??
      presets.value.some(
        (p) => (p.provider || p.login_provider) === provider.value && p.source === "user",
      ),
    set: (saved) => {
      sourceOverride.value = saved
    },
  })
  const selectedId = ref("")
  const refreshIndex = ref(0)
  const retryProviders = ref(0)
  const backend = computed(() => providers.value.find((b) => b.name === provider.value))
  const visible = computed(() => Boolean(backend.value) && !showSaved.value)
  const selected = computed(() => rows.value.find((row) => row.id === selectedId.value))

  watch(
    [open, retryProviders],
    async ([isOpen], _, onCleanup) => {
      let current = true
      onCleanup(() => {
        current = false
      })
      providersError.value = ""
      if (!isOpen || !api) {
        providers.value = []
        return
      }
      try {
        const { backends } = await api.getBackends()
        if (current) providers.value = backends.filter((b) => b.model_discovery)
      } catch (cause) {
        if (current)
          providersError.value =
            cause?.response?.data?.detail || "Could not load provider catalogs."
      }
    },
    { flush: "sync" },
  )

  watch(
    open,
    () => {
      sourceOverride.value = null
    },
    { flush: "sync" },
  )
  watch(
    [open, provider, visible, refreshIndex],
    ([isOpen, name, showCatalog, index], previous) => {
      const forced = previous !== undefined && index !== previous[3]
      if (!forced) selectedId.value = ""
      if (!isOpen || !showCatalog || !api) {
        entry.value = null
        return
      }
      entry.value = providerModelsEntry(api, name)
      loadProviderModels(api, name, { refresh: forced })
    },
    { flush: "sync" },
  )
  watch(rows, (list) => {
    if (selectedId.value && !list.some((row) => row.id === selectedId.value)) selectedId.value = ""
  })
  return {
    providers,
    backend,
    visible,
    showSaved,
    selectedId,
    selected,
    rows,
    busy,
    error,
    fetchedAt,
    providersError,
    refresh() {
      if (providersError.value) retryProviders.value++
      else refreshIndex.value++
    },
  }
}
