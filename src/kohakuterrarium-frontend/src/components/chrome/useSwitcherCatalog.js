import { computed, ref, watch } from "vue"

// Discovery is an optional host capability. Only the provider being viewed
// contacts its upstream, and only an explicit save can create a preset.
export function useSwitcherCatalog(api, open, provider) {
  const providers = ref([])
  const rows = ref([])
  const busy = ref(false)
  const error = ref("")
  const providersError = ref("")
  const showSaved = ref(false)
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
    [open, provider],
    () => {
      showSaved.value = false
    },
    { flush: "sync" },
  )
  watch(
    [open, provider, visible, refreshIndex],
    async ([isOpen, name, showCatalog], _, onCleanup) => {
      let current = true
      onCleanup(() => {
        current = false
      })
      rows.value = []
      selectedId.value = ""
      error.value = ""
      busy.value = isOpen && showCatalog
      if (!busy.value) return
      try {
        const { models } = await api.discoverModels(name)
        if (current) rows.value = models
      } catch (cause) {
        if (current)
          error.value = cause?.response?.data?.detail || "Could not load provider models."
      } finally {
        if (current) busy.value = false
      }
    },
    { flush: "sync" },
  )
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
    providersError,
    refresh() {
      if (providersError.value) retryProviders.value++
      else refreshIndex.value++
    },
  }
}
