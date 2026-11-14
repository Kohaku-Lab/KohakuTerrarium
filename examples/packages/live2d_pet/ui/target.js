import { computed, onScopeDispose, ref, watch } from "vue";
import { useWorkContext } from "@kohakuterrarium/ui-sdk";
import { settingsFor } from "./settings-state.js";

export function usePetTarget(kt, bridge) {
  const settings = settingsFor(kt);
  const selected = useWorkContext({ retainLast: true, includeSpeech: true });
  const resolved = ref(null);
  const fixed = computed(() => settings.state.value.controlMode === "agent");
  const active = computed(
    () =>
      bridge.ownership.owned.value &&
      (kt.isOverlayVisible?.("dragon") || bridge.rendering.value),
  );
  const pinned = useWorkContext({
    target: resolved,
    enabled: active,
    includeSpeech: true,
  });
  let generation = 0,
    pending = false,
    disposed = false;
  async function refresh() {
    const chosen = settings.state.value.companion;
    if (disposed || pending || !fixed.value || !active.value || !chosen) return;
    const mine = generation;
    pending = true;
    try {
      const { data } = await kt.api.get("companions");
      if (mine !== generation) return;
      resolved.value =
        data.companions.find((row) => row.creatureId === chosen.creatureId) ||
        null;
      bridge.targetMissing.value = !resolved.value;
    } catch {
      if (mine === generation) resolved.value = null;
    } finally {
      pending = false;
      if (mine !== generation) void refresh();
    }
  }
  const stop = watch(
    [
      () => fixed.value,
      () => active.value,
      () => settings.state.value.companion?.creatureId,
    ],
    () => {
      generation++;
      resolved.value = null;
      bridge.targetMissing.value = false;
      void refresh();
    },
    { immediate: true, flush: "sync" },
  );
  const timer = setInterval(refresh, 5000);
  onScopeDispose(() => {
    disposed = true;
    generation++;
    stop();
    clearInterval(timer);
  });
  return computed(() => (fixed.value ? pinned.value : selected.value));
}

export function targetView(bridge, preferences) {
  const fixed = preferences.controlMode === "agent";
  const target = fixed ? preferences.companion : bridge.work.value;
  const work = bridge.work.value;
  const label =
    (fixed && work?.creatureId === target?.creatureId && work?.label) ||
    target?.label ||
    target?.creatureId ||
    "";
  const actor =
    !fixed && work?.creatureLabel && work.creatureLabel !== label
      ? ` · ${work.creatureLabel}`
      : "";
  const opening =
    fixed && work?.creatureId === target?.creatureId ? work : target;
  return {
    text: target
      ? `${fixed ? "固定" : "跟随"}：${label}${actor}${fixed && bridge.targetMissing.value ? "（会话已结束）" : ""}`
      : fixed
        ? "尚未选择龙娘对话"
        : "尚未选择会话",
    target:
      bridge.targetMissing.value || !opening
        ? null
        : { sessionId: opening.sessionId, creatureId: opening.creatureId },
    controlledElsewhere:
      bridge.ownership.status.value === "other" &&
      !!bridge.ownership.current.value &&
      !bridge.desktop,
    controllerUnavailable:
      !bridge.desktop &&
      (bridge.ownership.status.value === "offline" ||
        (bridge.ownership.status.value === "other" &&
          !bridge.ownership.current.value)),
  };
}
