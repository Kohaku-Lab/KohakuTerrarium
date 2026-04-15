<template>
  <nav class="h-full flex flex-col border-r border-warm-200 dark:border-warm-700 bg-warm-100 dark:bg-warm-950 shrink-0 transition-all duration-200 overflow-hidden" :class="expanded ? 'w-52' : 'w-14'">
    <!-- Logo + toggle -->
    <div v-if="expanded" class="flex items-center gap-2 px-3 py-3 justify-between">
      <div class="flex items-center gap-2 min-w-0">
        <img src="/kohaku-icon.png" alt="Kohaku" class="w-7 h-7 rounded-full shrink-0 object-cover" />
        <span class="text-sm truncate"> <span class="font-bold text-amber">Kohaku</span><span class="font-light text-iolite-light dark:text-iolite-light">Terrarium</span> </span>
      </div>
      <button class="w-6 h-6 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors shrink-0" @click="expanded = false">
        <div class="i-carbon-side-panel-close text-sm" />
      </button>
    </div>
    <div v-else class="flex flex-col items-center gap-1 py-3">
      <img src="/kohaku-icon.png" alt="Kohaku" class="w-7 h-7 rounded-full object-cover" />
      <button class="w-6 h-6 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors" @click="expanded = true">
        <div class="i-carbon-side-panel-open text-sm" />
      </button>
    </div>

    <div class="mx-2 border-t border-warm-200 dark:border-warm-700" />

    <!-- Home -->
    <router-link v-slot="{ navigate, isExactActive }" to="/" custom>
      <NavItem :expanded="expanded" :active="isExactActive" icon="i-carbon-home" :label="t('common.home')" @click="navigate" />
    </router-link>

    <div class="mx-2 border-t border-warm-200 dark:border-warm-700 mt-1 mb-1" />

    <!-- Running instances directly listed -->
    <div v-if="expanded" class="px-3 mb-1">
      <span class="text-[10px] text-warm-400 uppercase tracking-wider font-medium">{{ t("common.running") }}</span>
    </div>

    <div class="flex-1 overflow-y-auto flex flex-col gap-0.5 min-h-0">
      <div v-if="instances.list.length === 0" class="px-3 py-2">
        <span v-if="expanded" class="text-xs text-warm-400">{{ t("common.noInstances") }}</span>
        <span v-else class="text-warm-400 text-[10px] text-center block">--</span>
      </div>
      <router-link v-for="inst in instances.list" :key="inst.id" v-slot="{ navigate, isActive }" :to="`/instances/${inst.id}`" custom>
        <NavItem :expanded="expanded" :active="isActive" :icon="inst.type === 'terrarium' ? 'i-carbon-network-4' : 'i-carbon-bot'" :label="inst.config_name" :status="inst.status" @click="navigate" />
      </router-link>
    </div>

    <div class="mx-2 border-t border-warm-200 dark:border-warm-700 mb-1" />

    <!-- Start new -->
    <router-link v-slot="{ navigate, isExactActive }" to="/new" custom>
      <NavItem :expanded="expanded" :active="isExactActive" icon="i-carbon-add-large" :label="t('common.startNew')" @click="navigate" />
    </router-link>

    <!-- Saved sessions -->
    <router-link v-slot="{ navigate, isExactActive }" to="/sessions" custom>
      <NavItem :expanded="expanded" :active="isExactActive" icon="i-carbon-recently-viewed" :label="t('common.sessions')" @click="navigate" />
    </router-link>

    <!-- Registry browser -->
    <router-link v-slot="{ navigate, isExactActive }" to="/registry" custom>
      <NavItem :expanded="expanded" :active="isExactActive" icon="i-carbon-catalog" :label="t('common.registry')" @click="navigate" />
    </router-link>

    <!-- Settings -->
    <router-link v-slot="{ navigate, isExactActive }" to="/settings" custom>
      <NavItem :expanded="expanded" :active="isExactActive" icon="i-carbon-settings" :label="t('common.settings')" @click="navigate" />
    </router-link>

    <div class="mx-2 border-t border-warm-200 dark:border-warm-700 mt-1 mb-1" />

    <!-- Theme toggle -->
    <NavItem :expanded="expanded" :active="false" :icon="theme.dark ? 'i-carbon-sun' : 'i-carbon-moon'" :label="theme.dark ? t('common.lightMode') : t('common.darkMode')" @click="theme.toggle()" />

    <div class="h-2" />
  </nav>
</template>

<script setup>
import { useThemeStore } from "@/stores/theme"
import { useInstancesStore } from "@/stores/instances"
import { useI18n } from "@/utils/i18n"
import { getHybridPrefSync, setHybridPref } from "@/utils/uiPrefs"

const theme = useThemeStore()
const instances = useInstancesStore()
const { t } = useI18n()

const expanded = ref(getHybridPrefSync("nav-expanded", true) !== false)

watch(expanded, (v) => {
  setHybridPref("nav-expanded", v)
})
</script>
