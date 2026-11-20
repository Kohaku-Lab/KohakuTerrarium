import { defineComponent, h, ref, onMounted } from "vue";
import { useExtension } from "@kohakuterrarium/ui-sdk";
import { models } from "./models/index.js";
import { settingsFor } from "./settings-state.js";

export const ModelResources = defineComponent({
  setup() {
    const kt = useExtension();
    const store = settingsFor(kt);
    const adapters = Object.values(models);
    const installed = ref({}),
      loaded = ref(false),
      busy = ref(false);
    const message = ref(""),
      failed = ref(false);
    onMounted(async () => {
      try {
        installed.value = (await kt.api.get("/models")).data;
        loaded.value = true;
      } catch {
        message.value = "资源状态读取失败，请重新打开设置。";
        failed.value = true;
      }
    });
    async function importFile(event) {
      const file = event.target.files?.[0];
      if (!file || busy.value) return;
      failed.value = false;
      if (file.size > 128 * 1024 * 1024) {
        message.value = "ZIP 不能超过 128 MiB。";
        failed.value = true;
        event.target.value = "";
        return;
      }
      busy.value = true;
      message.value = "正在导入并准备两档画质…";
      try {
        const { data } = await kt.api.post("/models", file, {
          headers: { "Content-Type": "application/zip" },
          timeout: 180000,
        });
        const model = data.model;
        if (
          !Object.hasOwn(models, model) ||
          models[model].bundled ||
          !/^[a-f0-9]{32}$/.test(data.version)
        )
          throw new Error("Invalid imported model");
        installed.value = { ...installed.value, [model]: data };
        loaded.value = true;
        store.update({
          model,
          models: { [model]: { resourceVersion: data.version } },
        });
        message.value = `导入完成，已切换到${models[model].shortName || models[model].name}。`;
      } catch (error) {
        const detail = error.response?.data?.detail;
        message.value =
          typeof detail === "string"
            ? detail
            : "导入失败，请检查资源包后重试。";
        failed.value = true;
      } finally {
        busy.value = false;
        event.target.value = "";
      }
    }
    return () =>
      h("fieldset", { class: "l2d-appearance l2d-model-resources" }, [
        h("legend", "模型资源"),
        h("strong", "已适配模型"),
        h(
          "p",
          { class: "l2d-resource-summary" },
          "适配与形象绑定，导入时自动识别。",
        ),
        h(
          "ul",
          { class: "l2d-adapters", "aria-label": "已有模型适配器" },
          adapters.map((adapter) =>
            h("li", { key: adapter.id }, [
              h("span", adapter.name),
              h(
                "small",
                adapter.bundled
                  ? "已内置 · 2K / 4K"
                  : !loaded.value
                    ? "资源状态待确认"
                    : installed.value[adapter.id]
                      ? "已导入 · 2K / 4K"
                      : "未导入资源",
              ),
            ]),
          ),
        ),
        h("div", { class: "l2d-resource-controls" }, [
          h(
            "label",
            { class: "l2d-import-button", "aria-disabled": busy.value },
            [
              h("span", busy.value ? "正在导入…" : "导入 L2D 模型"),
              h("input", {
                type: "file",
                accept: ".zip,application/zip",
                disabled: busy.value,
                "aria-label": "导入 L2D 模型",
                onChange: importFile,
              }),
            ],
          ),
          h(
            "small",
            "选择模型资源 ZIP（≤128 MiB），保留原目录结构。其他模型需先添加适配。",
          ),
        ]),
        message.value
          ? h(
              "p",
              {
                role: failed.value ? "alert" : "status",
                "aria-live": "polite",
                class: "l2d-resource-status",
              },
              message.value,
            )
          : null,
        h("small", "仅保存到当前 KT 用户目录；导入失败时保留已有资源。"),
      ]);
  },
});
