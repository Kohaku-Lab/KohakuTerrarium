import { defineComponent, h, ref } from "vue";
import { useExtension } from "@kohakuterrarium/ui-sdk";
import { settingsFor } from "./settings-state.js";
import { validateActionSet } from "./action-catalog.js";

export const ActionSettings = defineComponent({
  setup() {
    const kt = useExtension();
    const store = settingsFor(kt);
    const message = ref("");
    async function importFile(event) {
      const file = event.target.files?.[0];
      const model = store.state.value.model;
      if (!file) return;
      try {
        if (file.size > 65536) throw new Error("动作集文件不能超过 64 KiB。");
        const catalog = validateActionSet(JSON.parse(await file.text()), model);
        store.update({ models: { [model]: { actionSet: catalog } } });
        message.value = `已保存 ${catalog.label}；显示角色时会检查模型支持的动作。`;
      } catch (error) {
        message.value = error.message;
      }
      event.target.value = "";
    }
    async function exportFile() {
      const model = store.state.value.model;
      try {
        let catalog = store.state.value.models[model].actionSet;
        if (!catalog) {
          const response = await fetch(
            await kt.freshAssetUrl("action-sets.json"),
          );
          if (!response.ok) throw new Error("无法读取默认动作集。");
          catalog = (await response.json()).sets[model];
        }
        const url = URL.createObjectURL(
          new Blob([JSON.stringify(catalog, null, 2)], {
            type: "application/json",
          }),
        );
        const link = document.createElement("a");
        link.href = url;
        link.download = `${model}-actions.json`;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (error) {
        message.value = error.message;
      }
    }
    return () => {
      const { model, models } = store.state.value;
      return h("fieldset", { class: "l2d-appearance l2d-action-settings" }, [
        h("legend", "动作集"),
        h("div", { class: "l2d-action-summary" }, [
          h("strong", models[model].actionSet?.label || "内置动作集"),
          h("p", "自动状态和主动表达共用。可导出调整后，再导入当前角色。"),
        ]),
        h("div", { class: "l2d-action-buttons" }, [
          h("label", { class: "l2d-import-button" }, [
            h("span", "导入动作集"),
            h("input", {
              type: "file",
              accept: ".json,application/json",
              "aria-label": "导入动作集",
              onChange: importFile,
            }),
          ]),
          h("button", { type: "button", onClick: exportFile }, "导出动作集"),
          h(
            "button",
            {
              type: "button",
              onClick: () => {
                store.update({ models: { [model]: { actionSet: null } } });
                message.value = "已恢复内置动作集。";
              },
            },
            "恢复内置动作集",
          ),
        ]),
        message.value
          ? h(
              "p",
              { class: "l2d-action-status", role: "status" },
              message.value,
            )
          : null,
      ]);
    };
  },
});
