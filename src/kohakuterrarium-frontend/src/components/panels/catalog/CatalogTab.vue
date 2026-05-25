<template>
  <div class="h-full flex flex-col bg-warm-50 dark:bg-warm-900 overflow-hidden">
    <!-- Header row: tab toggle + search + actions -->
    <div class="flex items-center gap-2 px-3 pt-3 shrink-0 flex-wrap">
      <div class="flex items-center gap-0.5 rounded bg-warm-100 dark:bg-warm-800 p-0.5">
        <button v-for="t in tabs" :key="t.id" type="button" class="px-2.5 py-1 rounded text-[11px] font-medium transition-colors" :class="activeTab === t.id ? 'bg-white dark:bg-warm-900 text-iolite dark:text-iolite-light shadow-sm' : 'text-warm-600 dark:text-warm-400 hover:text-warm-800 dark:hover:text-warm-200'" @click="activeTab = t.id">
          {{ t.label }}
          <span v-if="t.count != null" class="ml-1 text-[10px] text-warm-500 dark:text-warm-500 font-normal">({{ t.count }})</span>
        </button>
      </div>

      <div class="relative flex-1 min-w-[12rem]">
        <span class="absolute left-2 top-1/2 -translate-y-1/2 i-carbon-search text-[12px] text-warm-400 pointer-events-none" aria-hidden="true" />
        <input v-model="query" type="search" class="input-field !pl-7 !py-1 !text-[11px]" :placeholder="searchPlaceholder" />
      </div>

      <button type="button" class="btn-icon !w-7 !h-7" title="Refresh" :disabled="loading" @click="onRefresh">
        <span class="i-carbon-renew text-[13px]" :class="{ 'kohaku-pulse': loading }" />
      </button>

      <button v-if="activeTab === 'installed'" type="button" class="btn-secondary !py-1 !px-2 !text-[11px]" :disabled="updatingAll || gitInstalled.length === 0" :title="gitInstalled.length === 0 ? 'No git-backed packages to update' : `Update all ${gitInstalled.length} git-backed package(s)`" @click="onUpdateAll">
        <span class="i-carbon-renew text-[10px] mr-1" :class="{ 'kohaku-pulse': updatingAll }" />
        Update all
      </button>

      <button type="button" class="btn-secondary !py-1 !px-2 !text-[11px]" title="Install from URL / local path / spec" @click="installFromSourceOpen = true">
        <span class="i-carbon-add text-[10px] mr-1" />
        Install from source
      </button>

      <button type="button" class="btn-icon !w-7 !h-7" title="Marketplace sources" @click="sourcesOpen = true">
        <span class="i-carbon-settings text-[13px]" />
      </button>
    </div>

    <!-- Tag chips (browse-tab only) -->
    <div v-if="activeTab === 'browse' && allTags.length" class="flex flex-wrap gap-1 shrink-0 px-3 pt-2">
      <button type="button" class="chip" :class="!activeTag ? 'chip-iolite' : 'chip-warm hover:bg-warm-200 dark:hover:bg-warm-700'" @click="activeTag = null">all</button>
      <button v-for="tag in allTags" :key="tag" type="button" class="chip" :class="activeTag === tag ? 'chip-iolite' : 'chip-warm hover:bg-warm-200 dark:hover:bg-warm-700'" @click="activeTag = activeTag === tag ? null : tag">
        {{ tag }}
      </button>
    </div>

    <!-- Status / error banner -->
    <div v-if="errorText" class="mx-3 mt-2 rounded border border-coral/40 px-3 py-2 text-[12px] text-coral shrink-0">
      {{ errorText }}
    </div>

    <!-- Content -->
    <div class="flex-1 overflow-y-auto p-3">
      <!-- Loading skeleton (only when truly empty) -->
      <div v-if="loading && currentList.length === 0" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
        <div v-for="i in 6" :key="i" class="card p-3 animate-pulse flex flex-col gap-2">
          <div class="flex gap-2">
            <div class="w-9 h-9 rounded-lg bg-warm-200 dark:bg-warm-800" />
            <div class="flex-1 space-y-1.5">
              <div class="h-3 w-1/2 bg-warm-200 dark:bg-warm-800 rounded" />
              <div class="h-2.5 w-3/4 bg-warm-200 dark:bg-warm-800 rounded" />
            </div>
          </div>
          <div class="space-y-1">
            <div class="h-2.5 bg-warm-200 dark:bg-warm-800 rounded" />
            <div class="h-2.5 w-5/6 bg-warm-200 dark:bg-warm-800 rounded" />
          </div>
        </div>
      </div>

      <!-- Empty state -->
      <div v-else-if="currentList.length === 0" class="flex flex-col items-center text-center gap-2 py-12">
        <span class="i-carbon-search-locate text-[36px] text-warm-400 dark:text-warm-600" aria-hidden="true" />
        <h3 class="text-[12px] font-semibold text-warm-700 dark:text-warm-300">
          {{ emptyTitle }}
        </h3>
        <p class="text-[11px] text-warm-500 dark:text-warm-400 max-w-xs">{{ emptyHint }}</p>
        <button v-if="query || activeTag" class="btn-secondary !py-1 !px-2.5 !text-[11px] mt-1" @click="clearFilters">Clear filters</button>
      </div>

      <!-- Browse: card grid -->
      <div v-else-if="activeTab === 'browse'" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
        <CatalogCard v-for="pkg in currentList" :key="pkgKey(pkg)" :pkg="pkg" mode="browse" :installed="isInstalled(pkg.name)" @install="onInstall" @tag-click="onTagClick" />
      </div>

      <!-- Installed: section header + accordion (one row per package,
           expand to see contributed creatures / terrariums / tools /
           plugins / llm_presets / io / triggers / skills / commands /
           user_commands / prompts). -->
      <div v-else class="flex flex-col gap-2">
        <h2 class="text-[12px] font-semibold text-warm-700 dark:text-warm-300 px-1">
          Installed packages
          <span class="text-[10px] font-normal text-warm-500 dark:text-warm-400"> ({{ currentList.length }}) </span>
        </h2>
        <InstalledPackagesAccordion :packages="currentList" :marketplace-by-name="marketplaceByName" :updating-set="updatingSet" @update="onUpdate" @uninstall="onUninstall" @edit-files="onEditFiles" />
      </div>
    </div>

    <!-- Status footer -->
    <div class="flex items-center justify-between text-[10px] text-warm-500 dark:text-warm-400 px-3 py-2 shrink-0 border-t border-warm-200 dark:border-warm-700">
      <span>
        {{ currentList.length }} shown
        <template v-if="activeTab === 'browse'">/ {{ marketplace.packages.length }} in marketplace</template>
        <template v-else>/ {{ installed.length }} installed</template>
      </span>
      <span v-if="marketplace.lastFetched">Marketplace refreshed {{ relativeTime(marketplace.lastFetched) }}</span>
    </div>

    <InstallConfirmModal :open="installOpen" :pkg="pendingInstall" @close="installOpen = false" @installed="onInstalled" />
    <InstallFromSourceModal :open="installFromSourceOpen" @close="installFromSourceOpen = false" @installed="onSourceInstalled" />
    <SourceListSettings :open="sourcesOpen" @close="sourcesOpen = false" />
    <PackageFilesDrawer v-model="filesOpen" :package-name="filesTarget" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue"
import { ElMessage, ElMessageBox } from "element-plus"

import CatalogCard from "@/components/panels/catalog/CatalogCard.vue"
import InstallConfirmModal from "@/components/panels/catalog/InstallConfirmModal.vue"
import InstallFromSourceModal from "@/components/panels/catalog/InstallFromSourceModal.vue"
import InstalledPackagesAccordion from "@/components/panels/catalog/InstalledPackagesAccordion.vue"
import SourceListSettings from "@/components/panels/catalog/SourceListSettings.vue"
import PackageFilesDrawer from "@/components/registry/PackageFilesDrawer.vue"
import { useMarketplaceStore } from "@/stores/marketplace"
import { packagesAPI, registryAPI } from "@/utils/api"

const marketplace = useMarketplaceStore()

const activeTab = ref("browse") // "browse" | "installed"
const query = ref("")
const activeTag = ref(null)
const sourcesOpen = ref(false)
const installOpen = ref(false)
const installFromSourceOpen = ref(false)
const pendingInstall = ref(null)
const filesOpen = ref(false)
const filesTarget = ref("")

// Installed-packages list — one row per kt package (NOT per
// creature/terrarium) so the Installed tab can render each package
// as an accordion whose body shows every manifest contribution
// (creatures, terrariums, tools, plugins, llm_presets, io,
// triggers, skills, commands, user_commands, prompts).  Backed by
// ``/api/studio/packages`` which wraps ``packages.walk.list_packages``.
const installed = ref([])
const installedLoading = ref(false)
const installedError = ref("")
const updatingSet = ref(new Set())
const updatingAll = ref(false)

const tabs = computed(() => [
  { id: "browse", label: "Browse", count: marketplace.packages.length || null },
  { id: "installed", label: "Installed", count: installed.value.length || null },
])

const loading = computed(() => (activeTab.value === "browse" ? marketplace.loading : installedLoading.value))

const errorText = computed(() => {
  const err = activeTab.value === "browse" ? marketplace.error : installedError.value
  if (!err) return ""
  return typeof err === "string" ? err : err?.message || String(err)
})

const allTags = computed(() => marketplace.allTags)
// Browse-side "Installed" badge: match the marketplace card by EITHER
// the install-dir name OR the manifest name (a rename via
// ``kt install --name X`` decouples them).
const installedNameSet = computed(() => {
  const s = new Set()
  for (const p of installed.value) {
    if (p.name) s.add(p.name)
    if (p.manifest_name) s.add(p.manifest_name)
  }
  return s
})
// "Update all" target list: non-editable packages.  ``registryAPI.updateAll``
// already filters out non-git internally, so we just exclude editable
// here.  Empty list → button is disabled.
const gitInstalled = computed(() => installed.value.filter((p) => !p.editable))

const searchPlaceholder = computed(() => {
  const n = activeTab.value === "browse" ? marketplace.packages.length : installed.value.length
  const what = activeTab.value === "browse" ? "marketplace" : "installed"
  return n ? `Search ${n} ${what} package${n === 1 ? "" : "s"}…` : `Search ${what}…`
})

const currentList = computed(() => {
  if (activeTab.value === "browse") {
    return marketplace.search({ query: query.value, tag: activeTag.value })
  }
  const q = query.value.trim().toLowerCase()
  return installed.value.filter((pkg) => {
    if (!q) return true
    return (pkg.name || "").toLowerCase().includes(q) || (pkg.description || "").toLowerCase().includes(q)
  })
})

const emptyTitle = computed(() => (activeTab.value === "browse" ? "No marketplace matches" : "No installed packages match"))
const emptyHint = computed(() => {
  if (activeTab.value === "browse") {
    if (marketplace.packages.length === 0) return "Click refresh to load the marketplace, or use Install from source for a git URL or local path."
    return "Try a different search term or clear the tag filter."
  }
  if (installed.value.length === 0) return "Browse the marketplace and click Install, or use Install from source for a git URL or local path."
  return "Try a different search term."
})

function pkgKey(pkg) {
  return `${pkg.name}:${pkg.version || pkg.versions?.[0]?.tag || "?"}`
}

function clearFilters() {
  query.value = ""
  activeTag.value = null
}

function isInstalled(name) {
  return installedNameSet.value.has(name)
}

// Bound store getter passed to the accordion so it can flag
// "update available" per-row without importing the store itself.
const marketplaceByName = (name) => marketplace.byName(name)

async function loadInstalled() {
  installedLoading.value = true
  installedError.value = ""
  try {
    const data = await packagesAPI.list()
    if (Array.isArray(data)) {
      installed.value = data
    } else if (data && typeof data === "object") {
      const out = []
      for (const [type, arr] of Object.entries(data)) {
        if (Array.isArray(arr)) for (const it of arr) out.push({ ...it, type })
      }
      installed.value = out
    } else {
      installed.value = []
    }
  } catch (err) {
    installedError.value = err?.response?.data?.detail || err?.message || String(err)
    installed.value = []
  } finally {
    installedLoading.value = false
  }
}

async function onRefresh() {
  if (activeTab.value === "browse") {
    await marketplace.invalidate()
    // Browse-tab refresh may surface new updates → re-scan installed
    // so "update available" badges stay accurate.
    await loadInstalled()
  } else {
    await loadInstalled()
  }
}

function onInstall(pkg) {
  // Browse-tab click → marketplace spec install confirm modal.
  pendingInstall.value = pkg
  installOpen.value = true
}

async function onInstalled() {
  await loadInstalled()
  activeTab.value = "installed"
}

async function onSourceInstalled() {
  installFromSourceOpen.value = false
  await loadInstalled()
  activeTab.value = "installed"
}

async function onUninstall(pkg) {
  try {
    await ElMessageBox.confirm(`Uninstall ${pkg.name}?`, "Confirm", {
      type: "warning",
      confirmButtonText: "Uninstall",
      cancelButtonText: "Cancel",
    })
  } catch {
    return
  }
  try {
    await registryAPI.uninstall(pkg.name)
    ElMessage.success(`${pkg.name} uninstalled`)
    await loadInstalled()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err?.message || String(err))
  }
}

async function onUpdate(pkg) {
  const next = new Set(updatingSet.value)
  next.add(pkg.name)
  updatingSet.value = next
  try {
    const r = await registryAPI.update(pkg.name)
    ElMessage.success(r?.message ? `${pkg.name}: ${r.message}` : `${pkg.name} updated`)
    await loadInstalled()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err?.message || String(err))
  } finally {
    const cleared = new Set(updatingSet.value)
    cleared.delete(pkg.name)
    updatingSet.value = cleared
  }
}

async function onUpdateAll() {
  if (gitInstalled.value.length === 0) return
  updatingAll.value = true
  try {
    const r = await registryAPI.updateAll()
    const lines = (r.messages || []).join("\n")
    await ElMessageBox.alert(lines || "Nothing to update.", `Updated ${r.updated || 0} · skipped ${r.skipped || 0}`, { confirmButtonText: "Close" }).catch(() => {})
    await loadInstalled()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err?.message || String(err))
  } finally {
    updatingAll.value = false
  }
}

function onEditFiles(pkg) {
  filesTarget.value = pkg.name
  filesOpen.value = true
}

function onTagClick(tag) {
  activeTag.value = tag
  activeTab.value = "browse"
}

function relativeTime(ts) {
  const diff = (Date.now() - ts) / 1000
  if (diff < 60) return "just now"
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`
  return new Date(ts).toLocaleString(undefined, { hour: "2-digit", minute: "2-digit" })
}

watch(
  activeTab,
  (tab) => {
    if (tab === "browse" && marketplace.packages.length === 0) {
      marketplace.fetch().catch(() => {})
    } else if (tab === "installed") {
      loadInstalled()
    }
  },
  { immediate: true },
)

onMounted(() => {
  marketplace.fetch().catch(() => {})
  loadInstalled()
})
</script>
