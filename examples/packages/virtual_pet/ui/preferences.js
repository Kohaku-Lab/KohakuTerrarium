import { computed, onUnmounted, ref, watch } from "vue";
import { useHost } from "@kohakuterrarium/ui-sdk";

const EVENT = "kt:pet-preferences";

/** Device-local gaze preference scoped to the current backend and user. */
export function usePetPreferences() {
  const host = useHost();
  const key = computed(
    () =>
      `kt.pet.gaze.${JSON.stringify([
        host.value.id,
        host.value.url,
        host.value.userId || "",
      ])}`,
  );
  const gazeEnabled = ref(true);
  function reload() {
    try {
      gazeEnabled.value = localStorage.getItem(key.value) !== "false";
    } catch {
      gazeEnabled.value = true;
    }
  }
  function setGaze(enabled) {
    try {
      localStorage.setItem(key.value, String(Boolean(enabled)));
    } catch {
      return false;
    }
    gazeEnabled.value = Boolean(enabled);
    window.dispatchEvent(new Event(EVENT));
    return true;
  }
  watch(key, reload, { immediate: true });
  window.addEventListener("storage", reload);
  window.addEventListener(EVENT, reload);
  onUnmounted(() => {
    window.removeEventListener("storage", reload);
    window.removeEventListener(EVENT, reload);
  });
  return { gazeEnabled, setGaze };
}
