import { computed, onUnmounted, ref, watch } from "vue";
import {
  ActionButton,
  SettingsGroup,
  SettingsRow,
  confirm,
  openTab,
  useHost,
  useOwner,
} from "@kohakuterrarium/ui-sdk";

import { PetSprite } from "./sprite.js";
import { decodeImage } from "./image.js";
import { text, usePetText } from "./strings.js";

export async function archiveBase64(
  file,
  maximum,
  invalid = text("en", "limits"),
) {
  if (!file.size || file.size > maximum) throw new Error(invalid);
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 8192) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
  }
  return btoa(binary);
}

const PetThumbnail = {
  props: {
    pet: { type: Object, required: true },
    active: Boolean,
    scope: String,
  },
  setup(props) {
    const owner = useOwner();
    const source = ref("");
    watch(
      () => [props.pet.key, props.active, props.scope],
      async (_, __, onCleanup) => {
        let stale = false;
        onCleanup(() => {
          stale = true;
        });
        source.value = "";
        if (!props.active) return;
        try {
          const image = await owner.call("thumbnail", { key: props.pet.key });
          if (!stale) source.value = image;
        } catch {
          /* The name remains available if a thumbnail cannot load. */
        }
      },
      { immediate: true },
    );
    return { source };
  },
  template: `<img v-if="source" :src="source" @error="source = ''" alt="" width="96" height="104" /><span v-else aria-hidden="true" class="vp-pet-mark">✦</span>`,
};

export const PetSettings = {
  name: "VirtualPetSettings",
  components: {
    PetSprite,
    PetThumbnail,
    ActionButton,
    SettingsGroup,
    SettingsRow,
  },
  props: { active: { type: Boolean, default: true } },
  setup(props) {
    const owner = useOwner();
    const host = useHost();
    const t = usePetText();
    const fileInput = ref(null);
    const busy = ref(false);
    const error = ref("");
    const previewError = ref("");
    const source = ref("");
    const revision = ref(0);
    const state = computed(() => owner.state.value || {});
    const pets = computed(() => state.value.pets || []);
    const selected = computed(() =>
      pets.value.find((pet) => pet.key === state.value.selected),
    );
    const scope = computed(() =>
      JSON.stringify([host.value.id, host.value.url, owner.status.value]),
    );
    let request = 0;
    function invalidate() {
      request += 1;
      busy.value = false;
      error.value = "";
    }
    watch(scope, invalidate, { flush: "sync" });
    onUnmounted(invalidate);
    watch(
      () =>
        JSON.stringify([
          scope.value,
          selected.value?.key,
          props.active,
          revision.value,
        ]),
      async (_, __, onCleanup) => {
        let stale = false;
        onCleanup(() => {
          stale = true;
        });
        source.value = "";
        previewError.value = "";
        if (!selected.value || !props.active) return;
        try {
          const image = await owner.call("sprite", { key: selected.value.key });
          if (stale) return;
          await decodeImage(image);
          if (!stale) source.value = image;
        } catch (err) {
          if (!stale) previewError.value = "previewFailed";
        }
      },
      { immediate: true },
    );

    async function perform(action, args) {
      if (busy.value || owner.status.value !== "ready") return;
      const current = ++request;
      busy.value = true;
      error.value = "";
      try {
        const payload = typeof args === "function" ? await args() : args;
        if (current !== request || payload === null) return;
        await owner.call(action, payload);
        if (current === request) await owner.refresh();
      } catch (err) {
        if (current === request) error.value = err.message || String(err);
      } finally {
        if (current === request) busy.value = false;
      }
    }
    async function importFile(event) {
      const file = event.target.files?.[0];
      event.target.value = "";
      if (!file) return;
      await perform("import", async () => ({
        archive: await archiveBase64(
          file,
          state.value.maxArchiveBytes || 8 * 1024 * 1024,
          t("limits"),
        ),
      }));
    }
    function remove(pet) {
      return perform("remove", async () =>
        (await confirm(
          t("removeConfirm", { name: pet.displayName }),
          t("removeTitle"),
        ))
          ? { key: pet.key }
          : null,
      );
    }
    return {
      t,
      fileInput,
      openOverlaySettings: () =>
        openTab({ kind: "settings", id: "settings", section: "overlays" }),
      owner,
      host,
      scope,
      pets,
      selected,
      source,
      busy,
      error,
      previewError,
      importFile,
      remove,
      select: (key) =>
        key !== selected.value?.key && perform("select", { key }),
      retry: async () => {
        error.value = "";
        const attempt = ++revision.value;
        const current = request;
        try {
          await owner.refresh();
        } catch (err) {
          if (current === request && attempt === revision.value)
            error.value = err.message || String(err);
        }
      },
    };
  },
  template: `
    <section class="vp-settings" :aria-label="t('pets')" :aria-busy="busy">
      <p class="vp-muted">{{ t('scope', {backend:host.name || t('backend')}) }}</p>
      <div v-if="owner.status.value === 'error' || owner.status.value === 'absent'" role="alert" class="vp-message">
        <span>{{ t('loadFailed') }}</span><ActionButton size="sm" @click="retry">{{ t('retry') }}</ActionButton>
      </div>
      <p v-else-if="owner.status.value !== 'ready'" role="status">{{ t('loading') }}</p>
      <template v-else>
        <div v-if="error" class="vp-message" role="alert"><span>{{ t('actionFailed') }} <small>{{ error }}</small></span><ActionButton size="sm" :disabled="busy" @click="retry">{{ t('retry') }}</ActionButton></div>
        <SettingsGroup :title="t('library')" :description="t('libraryHint')">
          <SettingsRow :title="t('import')">
            <template #detail><details class="vp-muted"><summary>{{ t('files') }}</summary><p>{{ t('filesHint') }}</p></details></template>
            <ActionButton variant="primary" :disabled="busy" @click="fileInput?.click()">{{ busy ? t('saving') : t('import') }}</ActionButton>
            <input ref="fileInput" type="file" accept=".zip,application/zip" tabindex="-1" hidden :disabled="busy" @change="importFile" />
          </SettingsRow>
          <p v-if="!pets.length" class="vp-welcome">{{ t('empty') }}</p>
          <div v-else class="vp-pet-grid" role="group" :aria-label="t('library')">
            <button v-for="pet in pets" :key="pet.key" type="button" class="vp-pet-card" :aria-label="t('select',{name:pet.displayName})" :aria-pressed="pet.key === selected?.key" :disabled="busy" @click="select(pet.key)">
              <PetThumbnail :pet="pet" :active="active" :scope="scope" /><span>{{ pet.displayName }}</span><small v-if="pet.key === selected?.key">{{ t('selected') }}</small>
            </button>
          </div>
        </SettingsGroup>
        <SettingsGroup v-if="selected" :title="t('preview')" :description="t('previewHint')">
          <div class="vp-preview">
            <div class="vp-preview-art">
              <PetSprite v-if="source && active" :key="selected.key" :atlas="selected" :source="source" />
              <p v-else-if="!previewError" role="status">{{ t('previewLoading') }}</p>
              <div v-if="previewError" class="vp-message" role="alert"><span>{{ t(previewError) }}</span><ActionButton size="sm" @click="retry">{{ t('retry') }}</ActionButton></div>
            </div>
            <div class="vp-preview-details"><h3>{{ selected.displayName }}</h3><p v-if="selected.description" class="vp-muted">{{ selected.description }}</p></div>
          </div>
          <SettingsRow :title="t('removeTitle')"><ActionButton variant="danger" class="vp-remove" :disabled="busy" @click="remove(selected)">{{ t('remove') }}</ActionButton></SettingsRow>
        </SettingsGroup>
        <SettingsGroup><SettingsRow :title="t('overlayTitle')" :hint="t('overlayHint')"><ActionButton @click="openOverlaySettings">{{ t('overlayControls') }}</ActionButton></SettingsRow></SettingsGroup>
      </template>
    </section>`,
};
