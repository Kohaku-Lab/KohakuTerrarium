import { computed, nextTick, onUnmounted, ref, watch } from "vue";
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
import { decodeImage, DEFAULT_HIT_BOUNDS, spriteHitBounds } from "./image.js";
import { PetSettings } from "./settings.js";
import { useBubble } from "./bubble.js";
import { usePetText } from "./strings.js";
import { usePetPreferences } from "./preferences.js";

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
    const { gazeEnabled } = usePetPreferences();
    const companion = ref(null);
    const controls = ref(false);
    const hovered = ref(false);
    const operating = ref(false);
    const focused = ref(false);
    const dismissed = ref(false);
    let operatingTarget = null;
    let controlTimer;
    const inTransit = computed(() => {
      const p = overlay.pointer.value;
      const r = companion.value?.getBoundingClientRect();
      return Boolean(
        p?.valid &&
        p.pointerType === "mouse" &&
        r &&
        p.x >= r.left &&
        p.x < r.left + r.width &&
        p.y >= r.top &&
        p.y < r.top + r.height,
      );
    });
    function syncControls() {
      clearTimeout(controlTimer);
      if (dismissed.value) return;
      if (hovered.value || operating.value || focused.value)
        controls.value = true;
      else if (!inTransit.value)
        controlTimer = setTimeout(() => {
          controls.value = false;
        }, 300);
    }
    function reveal(show) {
      hovered.value = show;
      if (show) dismissed.value = false;
      syncControls();
    }
    function operate(show, target = null) {
      operatingTarget = show ? target : null;
      operating.value = show;
      if (show) dismissed.value = false;
      syncControls();
    }
    function focusIn(event) {
      if (!companion.value.contains(event.relatedTarget))
        dismissed.value = false;
      focused.value = true;
      syncControls();
    }
    function focusOut(event) {
      focused.value = companion.value.contains(event.relatedTarget);
    }
    function dismissControls() {
      clearTimeout(controlTimer);
      dismissed.value = true;
      controls.value = false;
      if (companion.value.contains(document.activeElement))
        companion.value
          .querySelector(".vp-hit")
          ?.focus({ preventScroll: true });
    }
    watch([hovered, operating, focused, inTransit], syncControls);
    onUnmounted(() => clearTimeout(controlTimer));
    const source = ref("");
    const hitBounds = ref(DEFAULT_HIT_BOUNDS);
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
        host.value.userId,
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
          const decoded = await decodeImage(image);
          if (!stale) {
            hitBounds.value = spriteHitBounds(decoded, selected.value);
            const realm = JSON.stringify([
              host.value.id,
              host.value.url,
              host.value.userId,
            ]);
            greet.value =
              overlay.mode !== "window" &&
              appearances.get(realm) !== selected.value.key;
            appearances.set(realm, selected.value.key);
            source.value = image;
            await nextTick();
            if (!stale) overlay.ready?.();
          }
        } catch (err) {
          if (!stale) error.value = err.message || String(err);
        }
      },
      { immediate: true, flush: "sync" },
    );
    const feedback = useBubble(work, identity);
    watch(
      [feedback.status, source],
      () => {
        focused.value = Boolean(
          companion.value?.contains(document.activeElement),
        );
        if (operatingTarget && !operatingTarget.isConnected) operate(false);
      },
      { flush: "post" },
    );
    let dragOrigin = null;
    return {
      t,
      companion,
      controls,
      dismissed,
      operating,
      reveal,
      operate,
      focusIn,
      focusOut,
      dismissControls,
      gazeEnabled: computed(
        () => gazeEnabled.value && !operating.value && !focused.value,
      ),
      pointer: overlay.pointer,
      native: overlay.mode === "window",
      dismiss: () => {
        if (document.activeElement?.classList.contains("vp-dismiss"))
          companion.value
            ?.querySelector(".vp-hit")
            ?.focus({ preventScroll: true });
        feedback.dismiss();
        operate(false);
      },
      dismissible: computed(() => feedback.status.value === "failed"),
      selected,
      source,
      hitBounds,
      greet,
      error,
      identity,
      work,
      bubble: computed(() =>
        feedback.status.value ? t(feedback.status.value) : "",
      ),
      startDrag: () => {
        if (overlay.mode === "window") overlay.startDrag();
        else dragOrigin = { ...overlay.bounds.value };
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
    <section ref="companion" class="vp-companion" :class="{ 'vp-controls-open': controls, 'vp-controls-dismissed': dismissed }" :aria-label="t('companion')"
      @focusin="focusIn" @focusout="focusOut" @keydown.esc.stop="dismissControls">
      <span class="vp-sr-only" role="status" aria-live="polite" aria-atomic="true">{{ bubble }}</span>
      <div v-if="selected && source" class="vp-drag">
        <PetSprite :key="identity" :atlas="selected" :source="source" :hit-bounds="hitBounds" :work="work" :greet="greet" :pointer="pointer" :native="native" :gaze-enabled="gazeEnabled" fit interactive @hover="reveal" @drag-start="startDrag" @drag-move="moveDrag" />
      </div>
      <p v-else-if="error" class="kt-overlay-hit" role="alert">{{ t('imageFailed') }} <button type="button" @click="retry">{{ t('retry') }}</button></p>
      <button v-else-if="!selected" type="button" class="vp-choose kt-overlay-hit" @click="openSettings">{{ t('choose') }}</button>
      <div class="vp-controls kt-overlay-hit" @pointerenter="operate(true, $event.currentTarget)" @pointerleave="operate(false)">
        <button type="button" :aria-label="t('settings')" :title="t('settings')" @click="openSettings">⚙</button>
        <button type="button" :aria-label="t('hide')" :title="t('hide')" @click="hide">×</button>
      </div>
      <div class="vp-feedback">
        <div v-if="bubble" class="vp-bubble" :title="work?.label"><span aria-hidden="true">{{ bubble }}</span><button v-if="dismissible" type="button" class="vp-dismiss kt-overlay-hit" :aria-label="t('dismiss')" @pointerenter="operate(true, $event.currentTarget)" @pointerleave="operate(false)" @click="dismiss">×</button></div>
      </div>
    </section>`,
};

export default defineExtension((kt) => {
  kt.registerOverlay("pet", PetCompanion);
  kt.registerSettingsSection("pets", PetSettings);
});
