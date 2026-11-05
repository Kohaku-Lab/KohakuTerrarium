import { defineComponent, ref, watch, onUnmounted } from "vue";
import { useExtension, useWorkContext } from "@kohakuterrarium/ui-sdk";
import { settingsFor } from "./settings-state.js";

const bridges = new Map();
export function bridgeFor(kt) {
  const key = kt.scopeKey || kt.package || "live2d";
  if (bridges.has(key)) return bridges.get(key);
  const settings = settingsFor(kt);
  const desktop = new URL(location.href).searchParams.has("kt_overlay");
  const work = ref(null),
    command = ref(null),
    rendering = ref(false),
    connection = ref(null),
    renderError = ref(""),
    status = ref("自动动作");
  const peer = crypto.randomUUID();
  const channel =
    typeof BroadcastChannel === "function"
      ? new BroadcastChannel(`kt.live2d.${key}`)
      : null;
  let applying = false,
    rendererAt = 0,
    snapshotAt = 0;
  const send = (type, data) =>
    channel?.postMessage(JSON.parse(JSON.stringify({ type, data, peer })));
  const snapshot = () =>
    send("snapshot", { work: work.value, settings: settings.state.value });
  const stop = watch(
    settings.state,
    (value) => {
      if (!applying) send("settings", JSON.parse(JSON.stringify(value)));
    },
    { flush: "sync" },
  );
  if (channel)
    channel.onmessage = ({ data: packet }) => {
      if (!packet || packet.peer === peer) return;
      const { type, data } = packet;
      if (type === "hello" && !desktop) snapshot();
      if (type === "settings" || (type === "snapshot" && desktop)) {
        applying = true;
        settings.update(type === "settings" ? data : data.settings);
        applying = false;
      }
      if ((type === "snapshot" || type === "work") && desktop) {
        work.value = type === "snapshot" ? data.work : data;
        snapshotAt = Date.now();
      }
      if (type === "action") command.value = data;
      if (type === "renderer") {
        rendering.value = data.ready;
        renderError.value = data.error || "";
        rendererAt = Date.now();
      }
    };
  const tick = setInterval(() => {
    if (!desktop) snapshot();
    else if (Date.now() - snapshotAt > 15000) {
      work.value = null;
      command.value = { operation: "release", id: crypto.randomUUID() };
    }
    if (Date.now() - rendererAt > 12000) rendering.value = false;
  }, 5000);
  const result = {
    work,
    command,
    rendering,
    connection,
    renderError,
    status,
    desktop,
    snapshot,
    sendWork: () => send("work", work.value),
    renderer(value, error = "") {
      rendering.value = value;
      renderError.value = error;
      rendererAt = Date.now();
      send("renderer", { ready: value, error });
    },
    perform(value) {
      command.value = value;
      send("action", value);
    },
    close() {
      stop();
      clearInterval(tick);
      channel?.close();
      bridges.delete(key);
    },
  };
  bridges.set(key, result);
  // A package on a newly selected host has a separate transport and no old state.
  for (const [old, bridge] of bridges) if (old !== key) bridge.close();
  send("hello");
  window.addEventListener("pagehide", () => result.close(), { once: true });
  return result;
}

export const PetBridge = defineComponent({
  setup() {
    const kt = useExtension();
    const bridge = bridgeFor(kt);
    const settings = settingsFor(kt);
    const work = useWorkContext({ retainLast: true, includeSpeech: true });
    let lease = crypto.randomUUID(),
      ownedLease = null;
    let disposed = false,
      revision = 0,
      pending = Promise.resolve(),
      polling,
      pollAbort;
    const release = () => {
      bridge.connection.value = null;
      bridge.perform({ operation: "release", id: crypto.randomUUID() });
    };
    watch(
      work,
      (value) => {
        bridge.work.value = value;
        bridge.sendWork();
      },
      { immediate: true, flush: "sync" },
    );
    async function poll(generation, bindingLease) {
      while (
        !disposed &&
        generation === revision &&
        settings.state.value.agentControl &&
        bridge.rendering.value
      ) {
        try {
          pollAbort = new AbortController();
          const response = await kt.api.get("events", {
            params: { lease: bindingLease },
            signal: pollAbort.signal,
            timeout: 20000,
          });
          if (generation !== revision || disposed) return;
          const action = response.data.command;
          if (action && (!action.expiresAt || action.expiresAt > Date.now()))
            bridge.perform(action);
        } catch {
          if (generation === revision) {
            bridge.status.value = "动作连接待恢复";
            release();
          }
          return;
        }
      }
    }
    function sync(changed = false) {
      if (changed) {
        revision++;
        lease = crypto.randomUUID();
        pollAbort?.abort();
        release();
        bridge.status.value = "正在连接…";
      }
      const generation = revision;
      const current = work.value;
      const enabled =
        settings.state.value.agentControl &&
        bridge.rendering.value &&
        current?.connected &&
        !!current.creatureId;
      const body = {
        lease,
        enabled: !!enabled,
        model: settings.state.value.model,
        sessionId: current?.sessionId || "",
        creatureId: current?.creatureId || "",
        waiting: current?.status === "waiting",
        mode:
          settings.state.value.controlMode === "agent" ? "agent" : "expression",
        actionSet:
          settings.state.value.models[settings.state.value.model].actionSet,
      };
      pending = pending
        .catch(() => {})
        .then(async () => {
          if (disposed || generation !== revision) return;
          try {
            if (ownedLease && (ownedLease !== body.lease || !enabled)) {
              await kt.api.post("binding", {
                lease: ownedLease,
                enabled: false,
              });
              ownedLease = null;
            }
            if (disposed || generation !== revision) return;
            if (!enabled) {
              bridge.status.value = settings.state.value.agentControl
                ? "等待宠物和会话"
                : "自动动作";
              return;
            }
            const response = await kt.api.post("binding", body);
            ownedLease = body.lease;
            if (generation !== revision || disposed) return;
            bridge.connection.value = {
              sessionId: body.sessionId,
              creatureId: response.data.creatureId || body.creatureId,
              label: current.label || body.creatureId,
              mode: body.mode,
            };
            bridge.status.value = enabled
              ? "Agent 动作已连接"
              : settings.state.value.agentControl
                ? "等待宠物和会话"
                : "自动动作";
            if (enabled && polling !== generation) {
              polling = generation;
              void poll(generation, body.lease).finally(() => {
                if (polling === generation) polling = undefined;
              });
            }
          } catch (error) {
            if (generation === revision) {
              bridge.status.value =
                body.mode === "agent" && error.response?.status === 409
                  ? "请创建或打开龙娘对话。"
                  : "动作连接不可用";
              release();
            }
          }
        });
    }
    const unwatch = watch(
      [
        () => settings.state.value.agentControl,
        () => settings.state.value.controlMode,
        () =>
          JSON.stringify(
            settings.state.value.models[settings.state.value.model].actionSet,
          ),
        () => settings.state.value.model,
        () => bridge.rendering.value,
        () => work.value?.sessionId,
        () => work.value?.creatureId,
        () => work.value?.connected,
      ],
      () => sync(true),
      { immediate: true },
    );
    // Work settlement must not truncate a gesture the agent just requested.
    // Real waiting still wins in the player and gates new backend requests.
    watch(
      () => work.value?.status,
      () => sync(),
    );
    const heartbeat = setInterval(() => {
      if (settings.state.value.agentControl) sync();
    }, 5000);
    onUnmounted(() => {
      disposed = true;
      revision++;
      unwatch();
      clearInterval(heartbeat);
      pollAbort?.abort();
      release();
      void pending.finally(() =>
        ownedLease
          ? kt.api
              .post("binding", { lease: ownedLease, enabled: false })
              .catch(() => {})
          : undefined,
      );
    });
    return () => null;
  },
});
