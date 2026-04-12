<template>
  <nav
    class="h-full flex flex-col border-r border-warm-200 dark:border-warm-700 bg-warm-100 dark:bg-warm-950 shrink-0 transition-all duration-200 overflow-hidden"
    :class="expanded ? 'w-52' : 'w-14'"
  >
    <!-- Logo + toggle -->
    <div
      class="flex items-center gap-2 px-3 py-3"
      :class="expanded ? 'justify-between' : 'justify-center'"
    >
      <div class="flex items-center gap-2 min-w-0" v-if="expanded">
        <img
          src="/kohaku-icon.png"
          alt="Kohaku"
          class="w-7 h-7 rounded-full shrink-0 object-cover"
        />
        <span class="text-sm truncate">
          <span class="font-bold text-amber">Kohaku</span
          ><span class="font-light text-iolite-light dark:text-iolite-light">Terrarium</span>
        </span>
      </div>
      <img v-else src="/kohaku-icon.png" alt="Kohaku" class="w-7 h-7 rounded-full object-cover" />
      <button
        class="w-6 h-6 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors shrink-0"
        @click="expanded = !expanded"
      >
        <div
          :class="expanded ? 'i-carbon-side-panel-close' : 'i-carbon-side-panel-open'"
          class="text-sm"
        />
      </button>
    </div>

    <div class="mx-2 border-t border-warm-200 dark:border-warm-700" />

    <!-- Home -->
    <router-link to="/" custom v-slot="{ navigate, isExactActive }">
      <NavItem
        :expanded="expanded"
        :active="isExactActive"
        icon="i-carbon-home"
        label="Home"
        @click="navigate"
      />
    </router-link>

    <div class="mx-2 border-t border-warm-200 dark:border-warm-700 mt-1 mb-1" />

    <!-- Running instances directly listed -->
    <div v-if="expanded" class="px-3 mb-1">
      <span class="text-[10px] text-warm-400 uppercase tracking-wider font-medium">Running</span>
    </div>

    <div class="flex-1 overflow-y-auto flex flex-col gap-0.5 min-h-0">
      <div v-if="instances.list.length === 0" class="px-3 py-2">
        <span v-if="expanded" class="text-xs text-warm-400">No instances</span>
        <span v-else class="text-warm-400 text-[10px] text-center block">--</span>
      </div>
      <router-link
        v-for="inst in instances.list"
        :key="inst.id"
        :to="`/instances/${inst.id}`"
        custom
        v-slot="{ navigate, isActive }"
      >
        <NavItem
          :expanded="expanded"
          :active="isActive"
          :icon="inst.type === 'terrarium' ? 'i-carbon-network-4' : 'i-carbon-bot'"
          :label="inst.config_name"
          :status="inst.status"
          @click="navigate"
        />
      </router-link>
    </div>

    <div class="mx-2 border-t border-warm-200 dark:border-warm-700 mb-1" />

    <!-- Start new -->
    <router-link to="/new" custom v-slot="{ navigate, isExactActive }">
      <NavItem
        :expanded="expanded"
        :active="isExactActive"
        icon="i-carbon-add-large"
        label="Start New"
        @click="navigate"
      />
    </router-link>

    <!-- Saved sessions -->
    <router-link to="/sessions" custom v-slot="{ navigate, isExactActive }">
      <NavItem
        :expanded="expanded"
        :active="isExactActive"
        icon="i-carbon-recently-viewed"
        label="Sessions"
        @click="navigate"
      />
    </router-link>

    <!-- Registry browser -->
    <router-link to="/registry" custom v-slot="{ navigate, isExactActive }">
      <NavItem
        :expanded="expanded"
        :active="isExactActive"
        icon="i-carbon-catalog"
        label="Registry"
        @click="navigate"
      />
    </router-link>

    <!-- Settings -->
    <router-link to="/settings" custom v-slot="{ navigate, isExactActive }">
      <NavItem
        :expanded="expanded"
        :active="isExactActive"
        icon="i-carbon-settings"
        label="Settings"
        @click="navigate"
      />
    </router-link>

    <div class="mx-2 border-t border-warm-200 dark:border-warm-700 mt-1 mb-1" />

    <!-- Theme toggle -->
    <NavItem
      :expanded="expanded"
      :active="false"
      :icon="theme.dark ? 'i-carbon-sun' : 'i-carbon-moon'"
      :label="theme.dark ? 'Light Mode' : 'Dark Mode'"
      @click="theme.toggle()"
    />

    <div class="h-2" />
  </nav>
</template>

<script setup>
import { useThemeStore } from "@/stores/theme"
import { useInstancesStore } from "@/stores/instances"

const theme = useThemeStore()
const instances = useInstancesStore()

const expanded = ref(localStorage.getItem("nav-expanded") !== "false")

watch(expanded, (v) => {
  localStorage.setItem("nav-expanded", v ? "true" : "false")
})
</script>
