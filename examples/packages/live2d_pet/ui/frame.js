import { randomId } from "./id.js";
import { defineComponent, h, ref, watch, onMounted, onUnmounted } from "vue";
import { useExtension } from "@kohakuterrarium/ui-sdk";
import { validRegions, regionsClip } from "./crop.js";
import { bridgeFor } from "./bridge.js";
import { serveResource } from "./resource-loader.js";
import { models, modelAdapter } from "./models/index.js";
import { settingsFor } from "./settings-state.js";
import { targetView } from "./target.js";

export const PetFrame = defineComponent({
  props: { mini: Boolean, getViewport: Function },
  emits: ["model", "drag", "layout", "regions", "close"],
  setup(props, { emit }) {
    const kt = useExtension();
    const preferences = settingsFor(kt);
    const bridge = bridgeFor(kt);
    const work = bridge.work;
    const frame = ref(null);
    const src = ref("");
    const error = ref("");
    let resourceContext = null;
    let resourceAbort = new AbortController();
    const regions = ref([]);
    let cropSequence = 0;
    let settingsSequence = 0;

    let generation = 0;
    let disposed = false;
    let ready = false;
    let actionReady = false,
      actionError = "";
    let channel = "";
    let sequence = 0;
    const instance = randomId();
    function sendWork() {
      if (!ready || !src.value || !frame.value) return;
      const current = work.value;
      frame.value.contentWindow.postMessage(
        {
          type: "kt-live2d-work",
          channel,
          sequence: ++sequence,
          target: targetView(bridge, preferences.state.value),
          work: current
            ? {
                hostId: current.hostId,
                sessionId: current.sessionId,
                creatureId: current.creatureId,
                creatureLabel: current.creatureLabel,
                speech: current.speech ? { ...current.speech } : null,
                label: current.label,
                connected: current.connected,
                status: current.status,
                busy: current.busy,
                started: current.started,
                completed: current.completed,
              }
            : null,
        },
        new URL(src.value).origin,
      );
    }
    function sendSettings() {
      if (!ready || !src.value || !frame.value) return;
      frame.value.contentWindow.postMessage(
        {
          type: "kt-live2d-settings",
          channel,
          preferences: JSON.parse(JSON.stringify(preferences.state.value)),
        },
        new URL(src.value).origin,
      );
    }
    watch(
      preferences.state,
      (next, old) => {
        if (
          JSON.stringify(next.models[next.model].actionSet) !==
          JSON.stringify(old.models[old.model].actionSet)
        ) {
          actionReady = false;
          if (props.mini) bridge.renderer(false);
        }
        if (
          next.model !== old.model ||
          next.quality !== old.quality ||
          next.models[next.model].resourceVersion !==
            old.models[old.model].resourceVersion
        )
          void navigate();
        else sendSettings();
      },
      { flush: "sync" },
    );
    watch(work, sendWork, { flush: "sync" });
    watch(
      () => [
        preferences.state.value.controlMode,
        preferences.state.value.companion,
        bridge.targetMissing.value,
      ],
      sendWork,
    );
    watch(
      bridge.command,
      (command) => {
        if (ready && frame.value)
          frame.value.contentWindow.postMessage(
            {
              type: "kt-live2d-action",
              channel,
              command: JSON.parse(JSON.stringify(command)),
            },
            new URL(src.value).origin,
          );
      },
      { flush: "sync" },
    );
    let heartbeat,
      lastAlive = 0;
    function failRenderer(message) {
      ready = false;
      actionReady = false;
      clearInterval(heartbeat);
      resourceAbort.abort();
      error.value = message;
      src.value = "";
      if (props.mini) bridge.renderer(false, message);
    }
    function reportRegions() {
      const box = frame.value?.getBoundingClientRect();
      const parentBox = frame.value
        ?.closest(".kt-dragon-companion")
        ?.getBoundingClientRect();
      if (!box || !parentBox) return;
      emit(
        "regions",
        regions.value.map((r) => [
          box.left - parentBox.left + r.x * box.width,
          box.top - parentBox.top + r.y * box.height,
          r.width * box.width,
          r.height * box.height,
        ]),
      );
    }
    async function navigate() {
      const next = preferences.state.value;
      ready = false;
      actionReady = false;
      clearInterval(heartbeat);
      if (props.mini) bridge.renderer(false);
      const current = ++generation;
      error.value = "";
      resourceAbort.abort();
      resourceAbort = new AbortController();
      resourceContext = null;
      try {
        const url = new URL(
          await kt.freshAssetUrl("stage.html"),
          location.href,
        );
        let resource = null;
        if (!modelAdapter(next.model).bundled) {
          const { data } = await kt.api.get("/models", {
            signal: resourceAbort.signal,
          });
          resource = data[next.model];
          if (!resource) {
            if (!disposed && current === generation) src.value = "";
            throw new Error(
              "尚未导入此模型资源，请在 Live2D 设置中导入模型 ZIP。",
            );
          }
          url.searchParams.set("resourceVersion", resource.version);
        }
        if (disposed || current !== generation) return;
        ready = false;
        regions.value = [];
        cropSequence = 0;
        settingsSequence = 0;
        channel = `${instance}:${current}`;
        if (props.mini) url.searchParams.set("mini", "1");
        url.searchParams.set("v", "desktop-actions-1");
        url.searchParams.set(
          "preferences",
          JSON.stringify({
            ...next,
            models: Object.fromEntries(
              Object.entries(next.models).map(([name, value]) => [
                name,
                { ...value, actionSet: null },
              ]),
            ),
          }),
        );
        url.searchParams.set("channel", channel);
        if (next) {
          url.searchParams.set("pet", next.model);
          url.searchParams.set("quality", next.quality);
        }
        resourceContext = resource
          ? {
              channel,
              model: next.model,
              version: resource.version,
              signal: resourceAbort.signal,
            }
          : null;
        src.value = url.href;
      } catch (failure) {
        if (!disposed && current === generation)
          error.value = failure.message || "加载失败，请重试。";
      }
    }
    function receive(event) {
      if (
        !frame.value ||
        !src.value ||
        event.source !== frame.value.contentWindow ||
        event.origin !== new URL(src.value).origin
      )
        return;
      const data = event.data;
      if (data?.type === "kt-live2d-open-target" && data.channel === channel) {
        void bridge.openTarget();
        return;
      }
      if (data?.type === "kt-live2d-health" && data.channel === channel) {
        if (typeof data.error !== "string") return;
        if (data.error) failRenderer(data.error);
        else lastAlive = Date.now();
        return;
      }
      if (data?.type === "kt-live2d-menu-viewport") {
        if (
          !props.mini ||
          data.channel !== channel ||
          !Number.isSafeInteger(data.request) ||
          !Array.isArray(data.point) ||
          data.point.length !== 2 ||
          !data.point.every(Number.isFinite)
        )
          return;
        const source = event.source;
        const activeChannel = channel;
        const box = frame.value.getBoundingClientRect();
        const parentBox = frame.value
          .closest(".kt-dragon-companion")
          ?.getBoundingClientRect();
        const dx = box.left - (parentBox?.left || 0);
        const dy = box.top - (parentBox?.top || 0);
        Promise.resolve(
          props.getViewport?.(data.point[0] + dx, data.point[1] + dy),
        )
          .catch(() => null)
          .then((rect) => {
            if (
              disposed ||
              channel !== activeChannel ||
              frame.value?.contentWindow !== source
            )
              return;
            source.postMessage(
              {
                type: "kt-live2d-menu-viewport",
                channel,
                request: data.request,
                viewport: rect
                  ? [rect[0] - dx, rect[1] - dy, rect[2], rect[3]]
                  : null,
              },
              event.origin,
            );
          });
        return;
      }
      if (data?.type === "kt-live2d-resource") {
        if (resourceContext) void serveResource(kt, event, resourceContext);
        return;
      }
      if (data?.type === "kt-live2d-close") {
        if (props.mini && data.channel === channel) emit("close");
        return;
      }
      if (
        data?.type === "kt-live2d-actions-ready" &&
        data.channel === channel
      ) {
        const expected = JSON.stringify(
          preferences.state.value.models[preferences.state.value.model]
            .actionSet,
        );
        if (data.catalogKey !== expected || typeof data.error !== "string")
          return;
        actionError = data.error;
        actionReady = !actionError;
        if (props.mini) bridge.renderer(ready && actionReady, actionError);
        return;
      }
      if (props.mini && data?.type === "kt-live2d-crop") {
        if (
          data.channel === channel &&
          Number.isSafeInteger(data.sequence) &&
          data.sequence > cropSequence &&
          validRegions(data.regions)
        ) {
          cropSequence = data.sequence;
          regions.value = data.regions;
          reportRegions();
          if (
            Number.isFinite(data.headerOffset) &&
            data.headerOffset >= 0 &&
            data.headerOffset < 10000
          )
            emit("layout", data.headerOffset);
        }
        return;
      }
      if (
        event.data?.type === "kt-live2d-ready" &&
        event.data.channel === channel
      ) {
        if (Object.hasOwn(models, data.model))
          emit("model", { model: data.model, aspect: data.aspect });
        ready = true;
        lastAlive = Date.now();
        if (props.mini) {
          bridge.renderer(actionReady, actionError);
          clearInterval(heartbeat);
          heartbeat = setInterval(() => {
            if (Date.now() - lastAlive > 10000)
              failRenderer("渲染器连接已中断，请重试。");
            else bridge.renderer(actionReady, actionError);
          }, 4000);
        }
        sendSettings();
        sendWork();
        return;
      }
      if (
        data?.type === "kt-live2d-drag" &&
        data.channel === channel &&
        props.mini
      ) {
        if (
          ["start", "move", "end"].includes(data.phase) &&
          Number.isFinite(data.dx) &&
          Number.isFinite(data.dy) &&
          Math.abs(data.dx) < 20000 &&
          Math.abs(data.dy) < 20000
        )
          emit("drag", data);
        return;
      }
      if (data?.type === "kt-live2d-preferences") {
        if (
          data.channel !== channel ||
          !Number.isSafeInteger(data.sequence) ||
          data.sequence <= settingsSequence ||
          data.model !== preferences.state.value.model ||
          !data.patch ||
          typeof data.patch !== "object"
        )
          return;
        settingsSequence = data.sequence;
        preferences.update(data.patch);
        return;
      }
      if (event.data?.type !== "kt-live2d-navigate") return;
      const { model, quality } = event.data;
      if (
        !Object.hasOwn(models, model) ||
        !["standard", "high"].includes(quality)
      )
        return;
      preferences.update({ model, quality });
    }
    onMounted(() => {
      window.addEventListener("message", receive);
      void navigate();
    });
    onUnmounted(() => {
      disposed = true;
      resourceAbort.abort();
      clearInterval(heartbeat);
      if (props.mini) bridge.renderer(false);
      window.removeEventListener("message", receive);
    });
    return () =>
      h("div", { class: "kt-pet-frame-container" }, [
        error.value
          ? h(
              "div",
              { role: "alert", class: "kt-dragon-toolbar kt-overlay-hit" },
              [
                h("span", error.value),
                h("button", { onClick: () => navigate() }, "重试"),
              ],
            )
          : null,
        src.value
          ? h("iframe", {
              ref: frame,
              src: src.value,
              title: props.mini ? "Live2D 小宠物" : "Live2D 宠物互动预览",
              class: ["kt-dragon-frame", props.mini && "kt-overlay-hit"],
              style: props.mini
                ? { clipPath: regionsClip(regions.value) }
                : undefined,
            })
          : null,
      ]);
  },
});
