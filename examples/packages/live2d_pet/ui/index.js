import { bridgeFor } from "./bridge.js";
import { PetBridge, runtimeFor } from "./runtime.js";
import { fittedSize } from "./preferences.js";
import { settingsFor, initializeSettings } from "./settings-state.js";
import { PetSettings } from "./settings.js";
import { PetFrame } from "./frame.js";
import { defineComponent, h, ref, watch, onUnmounted, computed } from "vue";
import {
  defineExtension,
  useExtension,
  useOverlay,
  openTab,
} from "@kohakuterrarium/ui-sdk";

const companionVisible = ref(false);

const Preview = defineComponent({
  setup() {
    const kt = useExtension();
    const preferences = settingsFor(kt);
    const bridge = bridgeFor(kt);
    const visible = computed(
      () => kt.isOverlayVisible?.("dragon") ?? companionVisible.value,
    );
    return () =>
      !preferences.state.value.debug
        ? h("div", { class: "l2d-settings-page" }, [h(PetSettings)])
        : h("div", { class: "kt-dragon-demo" }, [
            h("div", { class: "kt-dragon-toolbar" }, [
              h("span", "Live2D 宠物 · Debug"),
              h(
                "button",
                {
                  onClick: () =>
                    openTab({
                      kind: "settings",
                      id: "settings",
                      section: `ext:${kt.package}/preferences`,
                    }),
                },
                "Live2D 设置",
              ),
              h(
                "button",
                { onClick: () => kt.toggleOverlay("dragon") },
                visible.value ? "收起小宠物，回到预览" : "放到 KT 角落",
              ),
            ]),
            visible.value || bridge.rendering.value || !bridge.canRender.value
              ? h(
                  "div",
                  { class: "kt-dragon-placeholder" },
                  "点击小宠物互动，右键选择动作。收起后可切换龙娘／大肥鱼，并比较日常与高清画质。",
                )
              : h(PetFrame),
          ]);
  },
});

const Companion = defineComponent({
  setup() {
    const kt = useExtension();
    const overlay = useOverlay();
    const preferences = settingsFor(kt);
    const bridge = bridgeFor(kt);
    const aspect = ref(null);
    const headerOffset = ref(0);
    watch(
      () => [
        preferences.state.value.model,
        preferences.state.value.quality,
        preferences.state.value.debug,
      ],
      () => {
        headerOffset.value = 0;
      },
    );
    let dragOrigin;
    const viewSize = () =>
      overlay.mode === "window"
        ? { width: screen.availWidth, height: screen.availHeight }
        : { width: innerWidth, height: innerHeight };
    const viewport = ref(viewSize());
    const resized = () => {
      viewport.value = viewSize();
    };
    window.addEventListener("resize", resized);
    const resize = () =>
      overlay.resize(
        ...fittedSize(
          preferences.state.value,
          viewport.value.width,
          viewport.value.height,
          aspect.value,
        ),
      );
    watch(
      () => [
        preferences.state.value.model,
        preferences.state.value.debug,
        aspect.value,
        preferences.state.value.models[preferences.state.value.model].scale,
        viewport.value.width,
        viewport.value.height,
        overlay.visible.value,
      ],
      resize,
      { immediate: true },
    );
    onUnmounted(() => {
      window.removeEventListener("resize", resized);
      overlay.setHitRegions?.(null);
    });
    watch(
      overlay.visible,
      (visible) => {
        companionVisible.value = visible;
      },
      { immediate: true },
    );
    onUnmounted(() => {
      companionVisible.value = false;
    });
    return () =>
      h("div", { class: "kt-dragon-companion" }, [
        preferences.state.value.debug
          ? h(
              "div",
              {
                class: "kt-dragon-handle kt-overlay-hit",
                style: {
                  transform: `translateY(${headerOffset.value}px)`,
                  zIndex: 1,
                },
                onPointerdown: overlay.startDrag,
              },
              [
                h(
                  "span",
                  { title: "拖动此处移动宠物" },
                  "✦ Live2D 宠物 · 拖动",
                ),
                h(
                  "button",
                  {
                    title: "Live2D 设置",
                    "aria-label": "Live2D 设置",
                    onPointerdown: (event) => event.stopPropagation(),
                    onClick: () =>
                      openTab({
                        kind: "settings",
                        id: "settings",
                        section: `ext:${kt.package}/preferences`,
                      }),
                  },
                  "⚙",
                ),
                h(
                  "button",
                  {
                    title: "收起宠物",
                    onPointerdown: (event) => event.stopPropagation(),
                    onClick: overlay.hide,
                  },
                  "×",
                ),
              ],
            )
          : null,
        overlay.visible.value && bridge.canRender.value
          ? h(PetFrame, {
              mini: true,
              getViewport: overlay.getViewport,
              onClose: overlay.hide,
              onLayout: (offset) => {
                headerOffset.value = offset;
              },
              onRegions: (regions) => {
                if (regions && preferences.state.value.debug) {
                  const width = overlay.bounds.value.width;
                  regions.push([
                    Math.max(0, (width - 280) / 2),
                    headerOffset.value,
                    Math.min(width, 280),
                    32,
                  ]);
                }
                overlay.setHitRegions?.(regions);
              },
              onModel: (info) => {
                if (info.model === preferences.state.value.model) {
                  aspect.value = info.aspect;
                  resize();
                }
              },
              onDrag: (event) => {
                if (event.phase === "start") {
                  dragOrigin = { ...overlay.bounds.value };
                  if (overlay.mode === "window") overlay.startDrag();
                }
                if (
                  event.phase === "move" &&
                  dragOrigin &&
                  overlay.mode !== "window"
                )
                  overlay.move(
                    dragOrigin.left + event.dx,
                    dragOrigin.top + event.dy,
                  );
                if (event.phase === "end") dragOrigin = null;
              },
            })
          : null,
      ]);
  },
});

export default defineExtension(async (kt) => {
  const settings = await initializeSettings(kt);
  if (kt.signal?.aborted) return;
  if (kt.consumeStartupFallback?.("dragon"))
    settings.update({ model: "dragon" });
  const params = new URLSearchParams(location.search);
  if (!params.has("kt_overlay") && !params.has("kt_popout")) runtimeFor(kt);
  else bridgeFor(kt);
  kt.registerTab("preview", Preview);
  kt.registerSettingsSection("preferences", PetSettings);
  kt.registerOverlay("dragon", Companion, {
    settingsSection: "preferences",
    controller: PetBridge,
  });
});
