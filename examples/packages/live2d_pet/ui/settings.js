import { modelAdapter } from "./models/index.js";
import { defineComponent, h } from "vue";
import { useExtension } from "@kohakuterrarium/ui-sdk";
import { bridgeFor } from "./bridge.js";
import { ModelResources } from "./model-resources.js";
import { ActionSettings } from "./action-settings.js";
import { ControlSettings } from "./control-settings.js";
import { settingsFor } from "./settings-state.js";
import {
  MODEL_NAMES,
  CONTROL_MODES,
  IDLE_CHOICES,
  expressionChoices,
  toggleAppearance,
} from "./preferences.js";

export const PetSettings = defineComponent({
  name: "Live2DPetSettings",
  setup() {
    const kt = useExtension();
    const store = settingsFor(kt);
    const bridge = bridgeFor(kt);
    const field = (label, control, hint = "") =>
      h("label", { class: "l2d-setting-row" }, [
        h("span", [label, hint ? h("small", hint) : null]),
        control,
      ]);
    const group = (title, children) =>
      h("section", { class: "l2d-setting-group", "aria-label": title }, [
        h("h3", title),
        ...children,
      ]);
    const toggle = (key, label, hint = "") =>
      field(
        label,
        h("input", {
          type: "checkbox",
          checked: store.state.value[key],
          "aria-label": label,
          onChange: (event) => store.update({ [key]: event.target.checked }),
        }),
        hint,
      );
    const select = (label, value, choices, change) =>
      h(
        "select",
        {
          "aria-label": label,
          value,
          onChange: (event) => change(event.target.value),
        },
        Object.entries(choices).map(([key, name]) =>
          h("option", { value: key }, name),
        ),
      );
    return () => {
      const prefs = store.state.value;
      const model = prefs.model;
      const current = prefs.models[model];
      const changeModel = (patch) =>
        store.update({ models: { [model]: patch } });
      return h(
        "section",
        { class: "l2d-settings", "aria-label": "Live2D 宠物设置" },
        [
          h(
            "p",
            "选择角色和默认形象。修改立即生效，并保存到当前 KT 的偏好设置；每个角色分别保存。",
          ),
          h("div", { class: "l2d-settings-actions" }, [
            h(
              "button",
              { onClick: () => kt.showOverlay("dragon") },
              "显示宠物",
            ),
            h(
              "button",
              { onClick: () => kt.hideOverlay("dragon") },
              "收起宠物",
            ),
          ]),
          group("角色与外观", [
            field(
              "角色",
              select("Live2D 角色", model, MODEL_NAMES, (value) =>
                store.update({ model: value }),
              ),
            ),
            h(ModelResources),
            field(
              "大小",
              h("span", { class: "l2d-size-control" }, [
                h("input", {
                  type: "range",
                  min: 50,
                  max: 300,
                  step: 5,
                  value: Math.round(current.scale * 100),
                  "aria-label": "宠物大小",
                  onInput: (event) =>
                    changeModel({ scale: Number(event.target.value) / 100 }),
                }),
                h("output", `${Math.round(current.scale * 100)}%`),
              ]),
              "100% 为当前推荐大小，超出窗口时自动适配。",
            ),
            field(
              "默认待机",
              select(
                "默认待机动作",
                current.idle,
                IDLE_CHOICES[model],
                (idle) => changeModel({ idle }),
              ),
              modelAdapter(model).interaction.sleep
                ? "闭眼打盹会优先于工作动作；选择其他待机可恢复互动。"
                : "使用模型自带的自然待机动作。",
            ),
            ...[true, false].map((appearance) => {
              const states = modelAdapter(model).appearanceStates || [];
              const matches = (name) => states.includes(name) === appearance;
              return h("fieldset", { class: "l2d-appearance" }, [
                h("legend", appearance ? "形象状态" : "默认姿势与表情"),
                appearance
                  ? h(
                      "p",
                      { class: "l2d-appearance-hint" },
                      "体型与装扮持续保留，不随休息、说话或动作切换。",
                    )
                  : null,
                ...Object.entries(expressionChoices(model))
                  .filter(([name]) => matches(name))
                  .map(([name, label]) =>
                    h(
                      "button",
                      {
                        "aria-pressed": current.expressions.includes(name),
                        onClick: () =>
                          changeModel({
                            expressions: toggleAppearance(prefs, model, name),
                          }),
                      },
                      label,
                    ),
                  ),
                h(
                  "button",
                  {
                    onClick: () =>
                      changeModel({
                        expressions: current.expressions.filter(
                          (name) => !matches(name),
                        ),
                      }),
                  },
                  appearance ? "恢复默认形象" : "恢复默认姿势与表情",
                ),
              ]);
            }),
            field(
              "画质",
              select(
                "宠物画质",
                prefs.quality,
                { high: "高品质 · 4K", standard: "性能优先 · 2K" },
                (quality) => store.update({ quality }),
              ),
              "高清适合放大查看，会增加显存占用。",
            ),
          ]),
          group("互动行为", [
            ...[
              ["followWork", "跟随会话动作"],
              ["talkingMouth", "说话口型"],
              ["tracking", "视线跟随"],
              ["physics", "头发与衣摆物理"],
              ...(modelAdapter(model).interaction.sleep
                ? [["autoSleep", "闲置一分钟自动打盹"]]
                : []),
            ].map(([key, label]) =>
              toggle(
                key,
                label,
                key === "talkingMouth"
                  ? "随回复正文开合嘴巴，遇标点停顿，结束或中断时收口。三种模式通用，不产生语音或额外模型调用。"
                  : "",
              ),
            ),
          ]),
          group("交互模式", [
            field(
              "当前模式",
              select(
                "交互模式",
                prefs.controlMode,
                CONTROL_MODES,
                (controlMode) => store.update({ controlMode }),
              ),
            ),
            h(ControlSettings),
          ]),
          h(ActionSettings),
          bridge.renderError.value
            ? h("p", { role: "alert" }, bridge.renderError.value)
            : null,
          group("高级设置", [
            toggle(
              "debug",
              "Debug 调试功能",
              "显示动作试演、画质比较、缩放检查和性能信息。",
            ),
            prefs.debug
              ? h(
                  "button",
                  { onClick: () => kt.openTab("preview") },
                  "打开调试预览",
                )
              : null,
          ]),
          h(
            "button",
            { onClick: () => store.reset(model) },
            "重置当前角色设置",
          ),
        ],
      );
    };
  },
});
