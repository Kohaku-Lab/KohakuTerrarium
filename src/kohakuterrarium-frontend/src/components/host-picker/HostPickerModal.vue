<template>
  <el-dialog v-model="visible" :title="t('hostPicker.title')" width="560px" :close-on-click-modal="true" align-center class="host-picker-dialog">
    <!-- Inner scroll container — Element Plus's default dialog grows
         freely with content, which pushes the page scrollbar on
         narrow viewports.  Cap at 70vh so long host lists + the QR
         scanner scroll INSIDE the modal instead of the page. -->
    <div class="flex flex-col gap-4 text-[13px] max-h-[70vh] overflow-y-auto pr-1 -mr-1">
      <!-- Saved hosts list -->
      <section class="flex flex-col gap-1">
        <h3 class="text-[11px] uppercase tracking-wider text-warm-400 font-medium px-1">
          {{ t("hostPicker.listTitle") }}
        </h3>
        <ul class="flex flex-col gap-1">
          <li>
            <button type="button" class="w-full flex items-center gap-2 px-2 py-1.5 rounded text-left transition-colors" :class="hosts.isSameOrigin ? 'bg-iolite/10 text-iolite dark:text-iolite-light' : 'text-warm-700 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800'" @click="onUseSameOrigin">
              <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="hosts.isSameOrigin ? 'bg-iolite' : 'bg-warm-400'" aria-hidden="true" />
              <span class="flex-1 min-w-0">
                <span class="font-medium block truncate">{{ t("hostPicker.sameOrigin") }}</span>
                <span class="block text-[11px] text-warm-400 truncate">{{ t("hostPicker.sameOriginHint") }}</span>
              </span>
              <span v-if="hosts.isSameOrigin" class="i-carbon-checkmark text-iolite text-[12px] shrink-0" aria-hidden="true" />
            </button>
          </li>
          <li v-for="h in hosts.hosts" :key="h.id">
            <div class="flex items-center gap-2 px-2 py-1.5 rounded transition-colors group" :class="hosts.activeHostId === h.id ? 'bg-iolite/10' : 'hover:bg-warm-100 dark:hover:bg-warm-800'">
              <button type="button" class="flex-1 min-w-0 flex items-center gap-2 text-left" :title="t('hostPicker.activate')" @click="onActivate(h.id)">
                <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="hosts.activeHostId === h.id ? 'bg-iolite' : 'bg-warm-400'" aria-hidden="true" />
                <span class="flex-1 min-w-0">
                  <span class="font-medium block truncate" :class="hosts.activeHostId === h.id ? 'text-iolite dark:text-iolite-light' : 'text-warm-700 dark:text-warm-300'">{{ h.name }}</span>
                  <span class="block text-[11px] text-warm-400 truncate font-mono">
                    {{ h.url }}<span v-if="h.token" class="text-aquamarine"> · {{ t("hostPicker.tokenSet") }}</span>
                  </span>
                </span>
                <span v-if="hosts.activeHostId === h.id" class="i-carbon-checkmark text-iolite text-[12px] shrink-0" aria-hidden="true" />
              </button>
              <button type="button" class="w-9 h-9 sm:w-6 sm:h-6 flex items-center justify-center rounded text-warm-400 hover:text-iolite hover:bg-warm-100 dark:hover:bg-warm-800 transition-colors hover-only-action" :title="t('hostPicker.editAction')" @click="onEdit(h)">
                <span class="i-carbon-edit text-sm sm:text-[11px]" />
              </button>
              <button type="button" class="w-9 h-9 sm:w-6 sm:h-6 flex items-center justify-center rounded text-warm-400 hover:text-coral hover:bg-warm-100 dark:hover:bg-warm-800 transition-colors hover-only-action" :title="t('hostPicker.removeAction')" @click="onRemove(h)">
                <span class="i-carbon-trash-can text-sm sm:text-[11px]" />
              </button>
            </div>
          </li>
          <li v-if="hosts.hosts.length === 0" class="text-[11px] text-warm-400 italic px-2 py-1">
            {{ t("hostPicker.noHosts") }}
          </li>
        </ul>
      </section>

      <!-- Add / edit form.  Uses the project's canonical ``input-field``
           UnoCSS shortcut (defined in ``uno.config.js``) — el-input
           inside an el-dialog teleports through the Element Plus
           theme bridge inconsistently across viewports, leaving the
           input background stuck on light-mode white in dark mode.
           The raw ``input`` + ``input-field`` shortcut binds bg /
           border / text / placeholder / focus to warm-* tokens
           directly, so it tracks the html.dark class reliably. -->
      <section class="flex flex-col gap-2 pt-3 border-t border-warm-200 dark:border-warm-700">
        <h3 class="text-[11px] uppercase tracking-wider text-warm-400 font-medium px-1">
          {{ isEditingId ? t("hostPicker.edit") : t("hostPicker.add") }}
        </h3>
        <div class="flex flex-col gap-2">
          <div>
            <label class="text-[11px] text-warm-400 block mb-1">{{ t("hostPicker.name") }}</label>
            <input v-model="form.name" type="text" autocomplete="off" class="input-field" :placeholder="t('hostPicker.namePlaceholder')" />
          </div>
          <div>
            <label class="text-[11px] text-warm-400 block mb-1">{{ t("hostPicker.url") }}</label>
            <input v-model="form.url" type="url" autocomplete="off" class="input-field font-mono" :placeholder="t('hostPicker.urlPlaceholder')" :disabled="isEditingId !== null" @keydown.enter="onSubmitForm" />
          </div>
          <div>
            <label class="text-[11px] text-warm-400 block mb-1">
              {{ t("hostPicker.token") }}
              <span class="text-warm-400 font-normal">{{ t("hostPicker.tokenOptional") }}</span>
            </label>
            <input v-model="form.token" type="password" autocomplete="off" class="input-field font-mono" :placeholder="t('hostPicker.tokenPlaceholder')" @keydown.enter="onSubmitForm" />
          </div>
          <p v-if="errorMessage" class="text-[12px] text-coral mt-1">{{ errorMessage }}</p>
          <div class="flex items-center justify-end gap-2 mt-1">
            <el-button v-if="isEditingId" size="small" @click="resetForm">{{ t("common.cancel") }}</el-button>
            <el-button v-if="isEditingId" size="small" type="primary" @click="onSaveEdit">
              {{ t("hostPicker.submitEdit") }}
            </el-button>
            <el-button v-else size="small" type="primary" :disabled="!isFormValid" @click="onSubmitForm">
              <span class="i-carbon-add mr-1" />
              {{ t("hostPicker.submitAdd") }}
            </el-button>
          </div>
        </div>
      </section>

      <!-- QR scanner -->
      <section v-if="!isEditingId" class="flex flex-col gap-2 pt-3 border-t border-warm-200 dark:border-warm-700">
        <h3 class="text-[11px] uppercase tracking-wider text-warm-400 font-medium px-1">
          {{ t("hostPicker.qrTitle") }}
        </h3>
        <QrScanner @scan="onQrScan" @cancel="() => {}" />
      </section>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue"
import { ElMessage, ElMessageBox } from "element-plus"

import QrScanner from "@/components/host-picker/QrScanner.vue"
import { useConnectIntent } from "@/composables/useConnectIntent"
import { useHostsStore } from "@/stores/hosts"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  open: { type: Boolean, default: false },
})
const emit = defineEmits(["close"])

const hosts = useHostsStore()
const { pendingUri, consume } = useConnectIntent()
const { t } = useI18n()

const visible = computed({
  get: () => props.open,
  set: (v) => {
    if (!v) emit("close")
  },
})

const form = ref({ name: "", url: "", token: "" })
const errorMessage = ref("")
const isEditingId = ref(null)

const isFormValid = computed(() => Boolean(form.value.url.trim()))

function resetForm() {
  form.value = { name: "", url: "", token: "" }
  errorMessage.value = ""
  isEditingId.value = null
}

function onSubmitForm() {
  try {
    errorMessage.value = ""
    const id = hosts.addHost(form.value)
    hosts.setActive(id)
    resetForm()
    emit("close")
  } catch (e) {
    errorMessage.value = e?.message || String(e)
  }
}

function onActivate(id) {
  hosts.setActive(id)
  emit("close")
}

function onUseSameOrigin() {
  hosts.setActive(null)
  emit("close")
}

async function onRemove(host) {
  try {
    await ElMessageBox.confirm(t("hostPicker.removeConfirm", { name: host.name }), t("hostPicker.removeAction"), {
      type: "warning",
      confirmButtonText: t("hostPicker.removeAction"),
      cancelButtonText: t("common.cancel"),
    })
  } catch {
    return
  }
  hosts.removeHost(host.id)
}

function onEdit(host) {
  isEditingId.value = host.id
  form.value = { name: host.name, url: host.url, token: host.token || "" }
  errorMessage.value = ""
}

function onSaveEdit() {
  try {
    errorMessage.value = ""
    if (!isEditingId.value) return
    hosts.updateHost(isEditingId.value, {
      name: form.value.name,
      token: form.value.token,
    })
    resetForm()
  } catch (e) {
    errorMessage.value = e?.message || String(e)
  }
}

function onQrScan(parsed) {
  // parsed: { url, token, scheme } from QrScanner.parseKtConnect
  form.value = {
    name: form.value.name || parsed.url,
    url: parsed.url,
    token: parsed.token,
  }
  onSubmitForm()
}

// Auto-consume a ktconnect:// URI delivered by Android deep-link.
watch(
  pendingUri,
  async (uri) => {
    if (!uri) return
    try {
      const u = new URL(uri)
      if (u.protocol !== "ktconnect:") throw new Error("not a ktconnect:// URI")
      const token = u.searchParams.get("token") || ""
      const scheme = (u.searchParams.get("scheme") || "http").toLowerCase()
      if (scheme !== "http" && scheme !== "https") {
        throw new Error(`unsupported scheme ${scheme}`)
      }
      const authority = u.host
      if (!authority) throw new Error("missing host:port")
      if (!token) throw new Error("missing token")
      const id = hosts.addHost({
        name: form.value.name || authority,
        url: `${scheme}://${authority}`,
        token,
      })
      hosts.setActive(id)
      ElMessage.success(t("hostPicker.qrFromUri", { host: authority }))
      consume()
      emit("close")
    } catch (e) {
      errorMessage.value = t("hostPicker.qrFailed", {
        error: e?.message || String(e),
      })
      consume()
    }
  },
  { immediate: true },
)
</script>
