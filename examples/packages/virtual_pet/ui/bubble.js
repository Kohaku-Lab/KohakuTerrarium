import { computed, onUnmounted, ref, watch } from "vue";

const DURATION = { ready: 3000, failed: 8000 };

/** View-local feedback lifetime, separate from the session's durable status. */
export function useBubble(work, scope) {
  const visible = ref("");
  let previous = null;
  let timer = null;
  let deadline = null;
  function clear() {
    clearTimeout(timer);
    timer = null;
    deadline = null;
    visible.value = "";
  }
  function expire() {
    if (deadline !== null && performance.now() >= deadline) clear();
  }
  watch(
    () =>
      JSON.stringify([
        scope.value,
        work.value?.hostId,
        work.value?.sessionId,
        work.value?.started,
        work.value?.status,
      ]),
    () => {
      const current = work.value;
      const same =
        current &&
        previous &&
        previous.scope === scope.value &&
        previous.hostId === current.hostId &&
        previous.sessionId === current.sessionId &&
        current.started >= previous.started;
      const changed =
        !same ||
        previous.started !== current.started ||
        previous.status !== current.status;
      if (!changed) return;
      clear();
      const status = current?.status;
      if (status === "running" || status === "waiting") visible.value = status;
      else if (same && DURATION[status]) {
        visible.value = status;
        deadline = performance.now() + DURATION[status];
        timer = setTimeout(clear, DURATION[status]);
      }
      previous = current ? { ...current, scope: scope.value } : null;
    },
    { immediate: true, flush: "sync" },
  );
  document.addEventListener("visibilitychange", expire);
  onUnmounted(() => {
    clear();
    document.removeEventListener("visibilitychange", expire);
  });
  return { status: computed(() => visible.value), dismiss: clear };
}
