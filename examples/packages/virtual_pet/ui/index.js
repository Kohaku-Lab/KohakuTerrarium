import { computed, ref, watch } from "vue";
import {
  defineExtension,
  openTab,
  useExtension,
  useHost,
  useOverlay,
  useOwner,
  useWorkContext,
} from "@kohakuterrarium/ui-sdk";

import { PetSprite } from "./sprite.js";
import { decodeImage } from "./image.js";
import { PetSettings } from "./settings.js";
import { useBubble } from "./bubble.js";
import { usePetText } from "./strings.js";

const appearances = new Map();

export const PetCompanion = {
  name: "VirtualPetCompanion",
  components: { PetSprite },
  setup() {
    const owner = useOwner();
    const host = useHost();
    const overlay = useOverlay();
    const extension = useExtension();
    const work = useWorkContext();
    const t = usePetText();
    const source = ref("");
    const greet = ref(false);
    const error = ref("");
    const revision = ref(0);
    const selected = computed(() => {
      const state = owner.state.value;
      return state?.pets?.find((pet) => pet.key === state.selected);
    });
    const identity = computed(() =>
      JSON.stringify([
        host.value.id,
        host.value.url,
        owner.status.value,
        selected.value?.key,
        revision.value,
      ]),
    );
    watch(
      identity,
      async (_, __, onCleanup) => {
        let stale = false;
        onCleanup(() => {
          stale = true;
        });
        source.value = "";
        error.value = "";
        if (!selected.value) return;
        try {
          const image = await owner.call("sprite", { key: selected.value.key });
          if (stale) return;
          await decodeImage(image);
          if (!stale) {
            const realm = JSON.stringify([host.value.id, host.value.url]);
            greet.value = appearances.get(realm) !== selected.value.key;
            appearances.set(realm, selected.value.key);
            source.value = image;
          }
        } catch (err) {
          if (!stale) error.value = err.message || String(err);
        }
      },
      { immediate: true, flush: "sync" },
    );
    const feedback = useBubble(work, identity);
    let dragOrigin = null;
    return {
      t,
      dismiss: feedback.dismiss,
      dismissible: computed(() => feedback.status.value === "failed"),
      selected,
      source,
      greet,
      error,
      identity,
      work,
      bubble: computed(() =>
        feedback.status.value ? t(feedback.status.value) : "",
      ),
      startDrag: () => {
        dragOrigin = { ...overlay.bounds.value };
      },
      moveDrag: ({ x, y }) => {
        if (dragOrigin) overlay.move(dragOrigin.left + x, dragOrigin.top + y);
      },
      hide: overlay.hide,
      retry: () => {
        revision.value++;
      },
      openSettings: () =>
        openTab({
          kind: "settings",
          id: "settings",
          section: `ext:${extension.package}/pets`,
        }),
    };
  },
  template: `
    <section class="vp-companion" :aria-label="t('companion')">
      <span class="vp-sr-only" role="status" aria-live="polite" aria-atomic="true">{{ bubble }}</span>
      <div class="vp-controls kt-overlay-hit">
        <button type="button" :aria-label="t('settings')" :title="t('settings')" @click="openSettings">⚙</button>
        <button type="button" :aria-label="t('hide')" :title="t('hide')" @click="hide">×</button>
      </div>
      <div v-if="bubble" class="vp-bubble" :title="work?.label"><span aria-hidden="true">{{ bubble }}</span><button v-if="dismissible" type="button" class="vp-dismiss kt-overlay-hit" :aria-label="t('dismiss')" @click="dismiss">×</button></div>
      <div v-if="selected && source" class="vp-drag">
        <PetSprite :key="identity" :atlas="selected" :source="source" :work="work" :greet="greet" fit interactive @drag-start="startDrag" @drag-move="moveDrag" />
      </div>
      <p v-else-if="error" class="kt-overlay-hit" role="alert">{{ t('imageFailed') }} <button type="button" @click="retry">{{ t('retry') }}</button></p>
      <button v-else-if="!selected" type="button" class="vp-choose kt-overlay-hit" @click="openSettings">{{ t('choose') }}</button>
    </section>`,
};

export default defineExtension((kt) => {
  kt.registerOverlay("pet", PetCompanion);
  kt.registerSettingsSection("pets", PetSettings);
});
