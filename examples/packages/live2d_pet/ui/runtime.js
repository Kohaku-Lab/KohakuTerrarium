import { defineComponent, effectScope, onScopeDispose, watch } from "vue";
import { useExtension } from "@kohakuterrarium/ui-sdk";
import { randomId } from "./id.js";
import { bridgeFor } from "./bridge.js";
import { settingsFor } from "./settings-state.js";
import { usePetTarget } from "./target.js";

const runtimes = new WeakMap();

// One controller per extension activation, independent of any settings/preview component.
export function runtimeFor(kt) {
  const bridge = bridgeFor(kt);
  if (runtimes.has(bridge)) return bridge;
  const scope = effectScope(true);
  const closeTransport = bridge.close;
  let disposed = false;
  bridge.close = () => {
    if (disposed) return;
    disposed = true;
    scope.stop();
    closeTransport();
    runtimes.delete(bridge);
  };
  runtimes.set(bridge, true);
  scope.run(() => startController(kt, bridge));
  kt.onDispose(bridge.close);
  return bridge;
}

export const PetBridge = defineComponent({
  setup() {
    runtimeFor(useExtension());
    return () => null;
  },
});

function startController(kt, bridge) {
  const settings = settingsFor(kt);
  void bridge.ownership.start();
  const work = usePetTarget(kt, bridge);
  let lease = randomId(),
    ownedLease = null,
    ownedController = "";
  let disposed = false,
    blocked = false,
    revision = 0,
    pending = Promise.resolve(),
    polling,
    pollAbort;
  const release = () => {
    bridge.connection.value = null;
    bridge.perform({ operation: "release", id: randomId() });
  };
  const stopWork = watch(
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
      } catch (error) {
        if (generation === revision && !disposed) {
          bridge.status.value = "动作连接待恢复";
          if (error.response?.status === 409) {
            blocked = true;
            ownedLease = null;
            bridge.connection.value = null;
          } else release();
        }
        return;
      }
    }
  }
  function sync(changed = false) {
    if (changed) {
      blocked = false;
      revision++;
      lease = randomId();
      pollAbort?.abort();
      if (ownedLease) release();
      bridge.status.value = "正在连接…";
    }
    const generation = revision;
    const current = work.value;
    const enabled =
      bridge.ownership.owned.value &&
      settings.state.value.agentControl &&
      bridge.rendering.value &&
      current?.connected &&
      !!current.creatureId;
    const body = {
      controller: bridge.ownership.current.value?.token || "",
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
              controller: ownedController,
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
          if (blocked) return;
          const response = await kt.api.post("binding", {
            ...body,
            renew: ownedLease === body.lease,
          });
          ownedLease = body.lease;
          ownedController = body.controller;
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
            blocked = error.response?.status === 409;
            bridge.status.value =
              body.mode === "agent" && error.response?.status === 409
                ? "请创建或打开龙娘对话，或关闭其他页面的主动表达。"
                : blocked
                  ? "其他页面正在控制，或租约已过期；请重新启用主动表达。"
                  : "动作连接不可用";
            bridge.connection.value = null;
            ownedLease = null;
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
      () => bridge.ownership.owned.value,
      () => bridge.ownership.current.value?.token,
      () => work.value?.sessionId,
      () => work.value?.creatureId,
      () => work.value?.connected,
    ],
    () => sync(true),
    { immediate: true },
  );
  // Work settlement must not truncate a gesture the agent just requested.
  // Real waiting still wins in the player and gates new backend requests.
  const stopStatus = watch(
    () => work.value?.status,
    () => sync(),
  );
  const heartbeat = setInterval(() => {
    if (settings.state.value.agentControl) sync();
  }, 5000);
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    revision++;
    unwatch();
    stopWork();
    stopStatus();
    clearInterval(heartbeat);
    pollAbort?.abort();
    if (ownedLease) release();
    bridge.close();
    void pending.finally(() =>
      ownedLease
        ? kt.api
            .post("binding", {
              controller: ownedController,
              lease: ownedLease,
              enabled: false,
            })
            .catch(() => {})
        : undefined,
    );
  };
  onScopeDispose(dispose);
}
