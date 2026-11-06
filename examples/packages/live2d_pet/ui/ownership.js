import { computed, ref } from "vue";

export function createOwnership(kt, client, observer, changed) {
  const current = ref(null);
  const status = ref("unmanaged");
  const owned = computed(() => current.value?.client === client);
  let disposed = false,
    pending = null;
  async function refresh(operation) {
    if (disposed || pending) return pending;
    const token = current.value?.token || "";
    pending = (async () => {
      try {
        const response = operation
          ? await kt.api.post("controller", { client, operation, token })
          : await kt.api.get("controller");
        if (disposed) {
          if (response.data.controller?.client === client)
            void kt.api
              .post("controller", {
                client,
                operation: "release",
                token: response.data.controller.token,
              })
              .catch(() => {});
          return;
        }
        current.value = response.data.controller;
        status.value = owned.value ? "owned" : "other";
      } catch (error) {
        if (disposed) return;
        current.value = error.response?.data?.detail?.controller || null;
        status.value = error.response?.status === 409 ? "other" : "offline";
      } finally {
        pending = null;
      }
    })();
    return pending;
  }
  const tick = () => refresh(owned.value ? "renew" : undefined);
  return {
    current,
    status,
    owned,
    start() {
      if (status.value !== "unmanaged") return;
      status.value = "connecting";
      return refresh(observer ? undefined : "claim");
    },
    tick,
    async takeover() {
      await pending;
      await refresh();
      await refresh("takeover");
      if (owned.value) changed();
    },
    refresh: () => refresh(),
    dispose() {
      if (disposed) return;
      disposed = true;
      if (owned.value)
        void kt.api
          .post("controller", {
            client,
            operation: "release",
            token: current.value.token,
          })
          .catch(() => {});
      current.value = null;
      status.value = "disposed";
    },
  };
}
