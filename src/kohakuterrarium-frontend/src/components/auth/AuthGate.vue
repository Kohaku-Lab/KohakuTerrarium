<template>
  <!-- Render children when the gate is open; otherwise display a
       full-screen LoginPane that blocks the rest of the UI.  The gate
       is closed when ``multi_user`` is ``required`` AND the active
       host has no stored userToken — that is the only mode where we
       hard-block UI access at the client (``optional`` lets anonymous
       users through; ``off`` never blocks). -->
  <template v-if="!isBlocked">
    <slot />
  </template>
  <div v-else class="fixed inset-0 z-50 bg-warm-50 dark:bg-warm-950 flex items-center justify-center p-4">
    <div class="w-full max-w-md flex flex-col gap-3 p-5 rounded-lg border border-warm-200 dark:border-warm-700 bg-warm-100 dark:bg-warm-900 shadow-lg">
      <p class="text-[12px] text-warm-400 italic px-1">
        {{ t("auth.gate.required") }}
      </p>
      <LoginPane :cancellable="false" @success="onSuccess" />
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import LoginPane from "@/components/auth/LoginPane.vue"
import { useAuthStore } from "@/stores/auth"
import { useI18n } from "@/utils/i18n"

const auth = useAuthStore()
const { t } = useI18n()

/** Block the app shell when the active host has multi_user=required
 *  AND we do not have a userToken stored on it.  Same-origin in
 *  required mode uses the cookie path and is not gated client-side
 *  (the backend 401 + ``X-Auth-Required: user`` interceptor will
 *  trigger a login prompt at first request instead).
 *
 *  Capabilities probing happens in ``App.vue`` (boot + activeHostId
 *  watcher) — this component reads from the auth store reactively
 *  and re-renders when the gate state changes. */
const isBlocked = computed(() => auth.needsLogin)

function onSuccess() {
  // LoginPane stored the token + user via auth.login(); nothing else
  // to do here — ``isBlocked`` flips automatically.
}
</script>
