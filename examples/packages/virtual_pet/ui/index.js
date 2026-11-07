import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
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
    const menu = ref(null);
    const menuOpen = ref(false);
    const menuPosition = ref({ left: "0%", top: "0%" });
    const operating = ref(false);
    const focused = ref(false);
    let operatingTarget = null;
    let menuAnchor = { x: 0, y: 0 };
    let menuObserver;
    function operate(show, target = null) {
      operatingTarget = show ? target : null;
      operating.value = show;
    }
    function closeMenu(restoreFocus = false) {
      if (!menuOpen.value) return;
      menuOpen.value = false;
      if (restoreFocus)
        companion.value
          ?.querySelector(".vp-hit")
          ?.focus({ preventScroll: true });
      else if (menu.value?.contains(document.activeElement))
        document.activeElement.blur();
    }
    function placeMenu() {
      if (!menuOpen.value || !menu.value || !companion.value) return;
      const r = companion.value.getBoundingClientRect();
      const m = menu.value.getBoundingClientRect();
      if (!r.width || !r.height) return;
      const x = Math.max(
        0,
        Math.min(r.width - m.width - 8, Math.max(8, menuAnchor.x * r.width)),
      );
      const y = Math.max(
        0,
        Math.min(r.height - m.height - 8, Math.max(8, menuAnchor.y * r.height)),
      );
      menuPosition.value = {
        left: `${(100 * x) / r.width}%`,
        top: `${(100 * y) / r.height}%`,
      };
    }
    async function openMenu(event) {
      const target = event.target?.closest?.(".vp-hit");
      if (!target || !companion.value?.contains(target)) return;
      event.preventDefault();
      event.stopPropagation();
      const r = companion.value.getBoundingClientRect();
      const h = target.getBoundingClientRect();
      const pointer =
        event.type === "contextmenu" && (event.clientX || event.clientY);
      menuAnchor = {
        x:
          ((pointer ? event.clientX : h.left + h.width / 2) - r.left) /
          (r.width || 1),
        y:
          ((pointer ? event.clientY : h.top + h.height) - r.top) /
          (r.height || 1),
      };
      menuOpen.value = true;
      await nextTick();
      if (!menuOpen.value || !menu.value) return;
      placeMenu();
      menu.value.scrollTop = 0;
      menu.value
        ?.querySelector('[role="menuitem"]')
        ?.focus({ preventScroll: true });
    }
    function contextKey(event) {
      if (
        !event.repeat &&
        (event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey))
      )
        void openMenu(event);
    }
    function menuKey(event) {
      if (["Escape", "Tab"].includes(event.key)) {
        if (event.key === "Escape") event.preventDefault();
        event.stopPropagation();
        closeMenu(true);
      } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        event.stopPropagation();
        const items = [...menu.value.querySelectorAll('[role="menuitem"]')];
        const current = items.indexOf(document.activeElement);
        const index =
          event.key === "Home"
            ? 0
            : event.key === "End"
              ? items.length - 1
              : (current +
                  (event.key === "ArrowDown" ? 1 : -1) +
                  items.length) %
                items.length;
        items[index]?.focus();
      }
    }
    function focusOut(event) {
      focused.value = companion.value.contains(event.relatedTarget);
      if (
        menuOpen.value &&
        menu.value?.contains(event.target) &&
        !menu.value.contains(event.relatedTarget)
      )
        closeMenu();
    }
    function outsideMenu(event) {
      if (!menu.value?.contains(event.target)) closeMenu();
    }
    function deactivate() {
      closeMenu();
      focused.value = false;
      operate(false);
    }
    function visibilityChanged() {
      if (document.hidden) deactivate();
    }
    onMounted(() => {
      menuObserver = new ResizeObserver(placeMenu);
      menuObserver.observe(companion.value);
      menuObserver.observe(menu.value);
      document.addEventListener("pointerdown", outsideMenu, true);
      document.addEventListener("visibilitychange", visibilityChanged);
      window.addEventListener("blur", deactivate);
    });
    onUnmounted(() => {
      menuObserver?.disconnect();
      document.removeEventListener("pointerdown", outsideMenu, true);
      document.removeEventListener("visibilitychange", visibilityChanged);
      window.removeEventListener("blur", deactivate);
    });
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
        closeMenu();
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
              (overlay.mode !== "window" || appearances.has(realm)) &&
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
      menu,
      menuOpen,
      menuPosition,
      openMenu,
      contextKey,
      menuKey,
      focused,
      operate,
      focusOut,
      gazeEnabled: computed(
        () =>
          gazeEnabled.value &&
          !menuOpen.value &&
          !operating.value &&
          !focused.value,
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
      hide: () => {
        closeMenu();
        overlay.hide();
      },
      retry: () => {
        revision.value++;
      },
      openSettings: () => {
        closeMenu();
        return openTab({
          kind: "settings",
          id: "settings",
          section: `ext:${extension.package}/pets`,
        });
      },
    };
  },
  template: `
    <section ref="companion" class="vp-companion" :aria-label="t('companion')"
      @focusin="focused = true" @focusout="focusOut" @contextmenu="openMenu" @keydown="contextKey">
      <span class="vp-sr-only" role="status" aria-live="polite" aria-atomic="true">{{ bubble }}</span>
      <div v-if="selected && source" class="vp-drag">
        <PetSprite :key="identity" :atlas="selected" :source="source" :hit-bounds="hitBounds" :work="work" :greet="greet" :pointer="pointer" :native="native" :gaze-enabled="gazeEnabled" :menu-open="menuOpen" menu-enabled fit interactive @drag-start="startDrag" @drag-move="moveDrag" />
      </div>
      <p v-else-if="error" class="kt-overlay-hit" role="alert">{{ t('imageFailed') }} <button type="button" @click="retry">{{ t('retry') }}</button></p>
      <button v-else-if="!selected" type="button" class="vp-choose kt-overlay-hit" @click="openSettings">{{ t('choose') }}</button>
      <div ref="menu" v-show="menuOpen" class="vp-menu kt-overlay-hit" role="menu" :aria-label="t('companion')" :style="menuPosition" @keydown="menuKey" @contextmenu.prevent.stop>
        <button type="button" role="menuitem" tabindex="-1" :aria-label="t('settings')" @click="openSettings">{{ t('settings') }}</button>
        <button type="button" role="menuitem" tabindex="-1" :aria-label="t('hide')" @click="hide">{{ t('hide') }}</button>
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
