import { randomId } from "./id.js";
import { computed, ref, watch } from "vue";
import { openChat } from "@kohakuterrarium/ui-sdk";
import { settingsFor } from "./settings-state.js";
import { createOwnership } from "./ownership.js";
import { targetView } from "./target.js";

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
    targetMissing = ref(false),
    renderError = ref(""),
    status = ref("自动动作");
  const peer = randomId();
  const channel =
    typeof BroadcastChannel === "function"
      ? new BroadcastChannel(`kt.live2d.${key}`)
      : null;
  let applying = false,
    closed = false,
    rendererAt = 0,
    snapshotAt = 0,
    workPeer = null;
  const retired = new Set();
  const ownership = createOwnership(kt, peer, desktop, () =>
    send("owner", null),
  );
  const send = (type, data) =>
    !closed &&
    channel?.postMessage(
      JSON.parse(
        JSON.stringify({
          type,
          data,
          peer,
          token: ownership.current.value?.token,
        }),
      ),
    );
  const snapshot = () =>
    ownership.owned.value &&
    send("snapshot", {
      work: work.value,
      settings: settings.state.value,
      targetMissing: targetMissing.value,
    });
  const stop = watch(
    settings.state,
    (value) => {
      if (!applying) send("settings", JSON.parse(JSON.stringify(value)));
    },
    { flush: "sync" },
  );
  if (channel)
    channel.onmessage = ({ data: packet }) => {
      if (closed || !packet || packet.peer === peer || retired.has(packet.peer))
        return;
      const { type, data } = packet;
      if (type === "owner") {
        void ownership.refresh();
        return;
      }
      if (
        ["snapshot", "work", "action", "renderer"].includes(type) &&
        (!packet.token || packet.token !== ownership.current.value?.token)
      )
        return;
      if (
        ["snapshot", "work", "action"].includes(type) &&
        packet.peer !== ownership.current.value?.client
      )
        return;
      if (type === "goodbye") {
        retired.add(packet.peer);
        if (retired.size > 256) retired.delete(retired.values().next().value);
        if (desktop && workPeer === packet.peer) {
          work.value = null;
          command.value = { operation: "release", id: randomId() };
        }
        return;
      }
      if (type === "hello" && !desktop) snapshot();
      if (type === "settings" || (type === "snapshot" && desktop)) {
        applying = true;
        settings.update(type === "settings" ? data : data.settings);
        applying = false;
      }
      if ((type === "snapshot" || type === "work") && desktop) {
        work.value = type === "snapshot" ? data.work : data;
        if (type === "snapshot") targetMissing.value = !!data.targetMissing;
        snapshotAt = Date.now();
        workPeer = packet.peer;
      }
      if (type === "action") command.value = data;
      if (type === "renderer") {
        rendering.value = data.ready;
        renderError.value = data.error || "";
        rendererAt = Date.now();
      }
    };
  const stopOwner = watch(
    () => ownership.current.value?.token,
    (_, previous) => {
      if (desktop) work.value = null;
      command.value = { operation: "release", id: randomId() };
      if (previous) rendering.value = false;
      connection.value = null;
      if (ownership.owned.value) snapshot();
      else if (desktop) send("hello");
    },
    { flush: "sync" },
  );
  const tick = setInterval(() => {
    if (ownership.status.value !== "unmanaged") void ownership.tick();
    if (!desktop) snapshot();
    else if (Date.now() - snapshotAt > 15000) {
      work.value = null;
      command.value = { operation: "release", id: randomId() };
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
    ownership,
    targetMissing,
    canRender: computed(
      () =>
        ownership.status.value === "unmanaged" ||
        (desktop ? !!ownership.current.value : ownership.owned.value),
    ),
    snapshot,
    openTarget() {
      const target = targetView(result, settings.state.value).target;
      if (target) return openChat(target);
    },
    sendWork: () => ownership.owned.value && send("work", work.value),
    renderer(value, error = "") {
      if (closed) return;
      rendering.value = value;
      renderError.value = error;
      rendererAt = Date.now();
      send("renderer", { ready: value, error });
    },
    perform(value) {
      if (closed) return;
      command.value = value;
      if (ownership.owned.value) send("action", value);
    },
    close() {
      if (closed) return;
      send("goodbye", null);
      closed = true;
      ownership.dispose();
      stop();
      stopOwner();
      clearInterval(tick);
      channel?.close();
      window.removeEventListener("pagehide", result.close);
      work.value = null;
      connection.value = null;
      rendering.value = false;
      command.value = null;
      if (bridges.get(key) === result) bridges.delete(key);
    },
  };
  bridges.set(key, result);
  if (desktop) void ownership.start();
  // A package on a newly selected host has a separate transport and no old state.
  for (const [old, bridge] of bridges) if (old !== key) bridge.close();
  send("hello");
  window.addEventListener("pagehide", result.close, { once: true });
  kt.onDispose?.(result.close);
  return result;
}
