<template>
  <!-- Hamburger button — sits inline in the parent header -->
  <button class="w-9 h-9 flex items-center justify-center rounded-lg text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800 shrink-0" @click="open = true">
    <div class="i-carbon-menu text-lg" />
  </button>

  <!-- Overlay (no Teleport — must stay inside #app for CSS zoom to apply) -->
  <Transition name="mnav">
    <div v-if="open" class="fixed inset-0 z-[100] flex" @keydown.escape="open = false">
      <!-- Backdrop -->
      <div class="absolute inset-0 bg-black/40" @click="open = false" />

      <!-- Slide-in panel -->
      <nav class="relative w-72 h-full flex flex-col bg-warm-50 dark:bg-warm-950 shadow-xl overflow-hidden">
        <!-- Header -->
        <div class="flex items-center gap-2 px-4 py-3 border-b border-warm-200 dark:border-warm-700">
          <img src="/kohaku-icon.png" alt="Kohaku" class="w-7 h-7 rounded-full object-cover" />
          <span class="text-sm flex-1 min-w-0"> <span class="font-bold text-amber">Kohaku</span><span class="font-light text-iolite-light">Terrarium</span> </span>
          <button class="w-8 h-8 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300" @click="open = false">
            <div class="i-carbon-close text-lg" />
          </button>
        </div>

        <!-- Home -->
        <a class="flex items-center gap-3 px-4 py-3 text-sm transition-colors cursor-pointer" :class="route.path === '/mobile' ? 'text-iolite bg-iolite/10' : 'text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800'" @click="go('/mobile')">
          <div class="i-carbon-home text-base" />
          <span>{{ t("common.home") }}</span>
        </a>

        <div class="mx-3 border-t border-warm-200 dark:border-warm-700" />

        <!-- Running instances -->
        <div class="px-4 py-2">
          <span class="text-[10px] text-warm-400 uppercase tracking-wider font-medium">{{ t("common.running") }}</span>
        </div>
        <div class="flex-1 overflow-y-auto min-h-0">
          <div v-if="instances.list.length === 0" class="px-4 py-3 text-xs text-warm-400">{{ t("common.noInstances") }}</div>
          <a v-for="inst in instances.list" :key="inst.id" class="flex items-center gap-3 px-4 py-2.5 text-sm cursor-pointer transition-colors" :class="route.params.id === inst.id ? 'text-iolite bg-iolite/10' : 'text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800'" @click="go(`/mobile/${inst.id}`)">
            <div :class="inst.type === 'terrarium' ? 'i-carbon-network-4' : 'i-carbon-bot'" class="text-base" />
            <span class="flex-1 truncate">{{ inst.config_name }}</span>
            <span class="w-2 h-2 rounded-full shrink-0" :class="inst.status === 'running' ? 'bg-aquamarine' : 'bg-warm-400'" />
          </a>
        </div>

        <div class="mx-3 border-t border-warm-200 dark:border-warm-700" />

        <!-- Bottom actions -->
        <a class="flex items-center gap-3 px-4 py-2.5 text-sm text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800 cursor-pointer" @click="go('/mobile/new')">
          <div class="i-carbon-add-large text-base" />
          <span>{{ t("common.startNew") }}</span>
        </a>
        <a class="flex items-center gap-3 px-4 py-2.5 text-sm text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800 cursor-pointer" @click="go('/mobile/sessions')">
          <div class="i-carbon-recently-viewed text-base" />
          <span>{{ t("common.sessions") }}</span>
        </a>
        <a class="flex items-center gap-3 px-4 py-2.5 text-sm text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800 cursor-pointer" @click="go('/mobile/registry')">
          <div class="i-carbon-catalog text-base" />
          <span>{{ t("common.registry") }}</span>
        </a>
        <a class="flex items-center gap-3 px-4 py-2.5 text-sm text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800 cursor-pointer" @click="go('/mobile/settings')">
          <div class="i-carbon-settings text-base" />
          <span>{{ t("common.settings") }}</span>
        </a>

        <div class="mx-3 border-t border-warm-200 dark:border-warm-700 mt-1" />

        <!-- Theme toggle -->
        <button class="flex items-center gap-3 px-4 py-2.5 mb-2 text-sm text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800 w-full" @click="theme.toggle()">
          <div :class="theme.dark ? 'i-carbon-sun' : 'i-carbon-moon'" class="text-base" />
          <span>{{ theme.dark ? t("common.lightMode") : t("common.darkMode") }}</span>
        </button>
      </nav>
    </div>
  </Transition>
</template>

<script setup>
import { ref } from "vue"

import { useInstancesStore } from "@/stores/instances"
import { useThemeStore } from "@/stores/theme"
import { useI18n } from "@/utils/i18n"

const route = useRoute()
const router = useRouter()
const instances = useInstancesStore()
const theme = useThemeStore()
const { t } = useI18n()

const open = ref(false)

function go(path) {
  open.value = false
  router.push(path)
}
</script>

<style scoped>
.mnav-enter-active,
.mnav-leave-active {
  transition: opacity 0.2s ease;
}
.mnav-enter-active nav,
.mnav-leave-active nav {
  transition: transform 0.2s ease;
}
.mnav-enter-from,
.mnav-leave-to {
  opacity: 0;
}
.mnav-enter-from nav,
.mnav-leave-to nav {
  transform: translateX(-100%);
}
</style>
