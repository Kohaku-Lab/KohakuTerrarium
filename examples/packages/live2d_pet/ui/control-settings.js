import { defineComponent, h, onUnmounted, ref, Teleport, watch } from "vue";
import {
  NewSessionDialog,
  openChat,
  useExtension,
} from "@kohakuterrarium/ui-sdk";
import { bridgeFor } from "./bridge.js";
import { settingsFor } from "./settings-state.js";

export const ControlSettings = defineComponent({
  name: "Live2DControlSettings",
  setup() {
    const kt = useExtension();
    const bridge = bridgeFor(kt);
    const store = settingsFor(kt);
    const creating = ref(false),
      busy = ref(false),
      error = ref("");
    const choices = ref([]),
      selected = ref("");
    let generation = 0;
    let navigation = new AbortController();
    const key = (row) => `${row.sessionId}/${row.creatureId}`;
    const open = async (row) => {
      if (await openChat(row, { signal: navigation.signal }))
        kt.showOverlay("dragon");
    };
    const run = async (action) => {
      if (busy.value) return;
      busy.value = true;
      error.value = "";
      const request = generation;
      try {
        await action(() => request === generation);
      } catch {
        if (request === generation) error.value = "无法打开对话，请重试。";
      } finally {
        if (request === generation) busy.value = false;
      }
    };
    const launch = () =>
      run(async (current) => {
        const response = await kt.api.get("companions");
        if (!current()) return;
        const rows = response.data.companions;
        const connected = bridge.connection.value;
        const existing =
          connected?.mode === "agent" &&
          rows.find(
            (row) =>
              row.sessionId === connected.sessionId &&
              row.creatureId === connected.creatureId,
          );
        if (existing || rows.length === 1) await open(existing || rows[0]);
        else if (!rows.length) {
          kt.showOverlay("dragon");
          creating.value = true;
        } else {
          choices.value = rows;
          selected.value = key(rows[0]);
        }
      });
    watch(
      () => store.state.value.controlMode,
      () => {
        generation++;
        navigation.abort();
        navigation = new AbortController();
        busy.value = false;
        creating.value = false;
        choices.value = [];
        error.value = "";
      },
    );
    onUnmounted(() => {
      generation++;
      navigation.abort();
    });
    return () => {
      const mode = store.state.value.controlMode;
      const connected = bridge.connection.value;
      const work = bridge.work.value;
      const label =
        connected &&
        (work?.sessionId === connected.sessionId
          ? work.label
          : connected.label);
      return h("div", { class: "l2d-control-settings" }, [
        h("dl", { class: "l2d-mode-guide" }, [
          h("dt", "普通 Pet"),
          h("dd", "按会话状态自动做动作，不增加模型调用。"),
          h("dt", "可选主动表达"),
          h("dd", "当前对话的 Agent 自选动作，沿用原模型；增加动作工具调用。"),
          h("dt", "专用 L2D Agent"),
          h("dd", "在龙娘对话里聊天，由同一个 Agent 回复并控制动作。"),
        ]),
        mode !== "pet"
          ? h(
              "p",
              { role: "status" },
              connected
                ? `当前连接：${label || connected.creatureId}`
                : `未连接 · ${bridge.status.value}`,
            )
          : null,
        mode === "agent"
          ? h("div", [
              h(
                "button",
                { type: "button", disabled: busy.value, onClick: launch },
                busy.value ? "正在打开…" : "创建 / 打开龙娘对话",
              ),
              h(
                "p",
                { class: "l2d-control-hint" },
                "优先打开运行中的龙娘对话，没有则新建。切换模式不会启动 Agent，也没有额外的后台 Agent；切到普通对话会断开动作连接。",
              ),
              choices.value.length
                ? h("div", { class: "l2d-settings-actions" }, [
                    h(
                      "select",
                      {
                        "aria-label": "龙娘对话",
                        value: selected.value,
                        onChange: (event) =>
                          (selected.value = event.target.value),
                      },
                      choices.value.map((row) =>
                        h("option", { value: key(row) }, row.label),
                      ),
                    ),
                    h(
                      "button",
                      {
                        type: "button",
                        disabled: busy.value,
                        onClick: () =>
                          run(() =>
                            open(
                              choices.value.find(
                                (row) => key(row) === selected.value,
                              ),
                            ),
                          ),
                      },
                      "打开对话",
                    ),
                    h(
                      "button",
                      {
                        type: "button",
                        disabled: busy.value,
                        onClick: () => (creating.value = true),
                      },
                      "新建对话",
                    ),
                  ])
                : null,
            ])
          : null,
        error.value ? h("p", { role: "alert" }, error.value) : null,
        creating.value
          ? h(
              Teleport,
              { to: "body" },
              h(NewSessionDialog, {
                initialConfig: `@${kt.package}/creatures/companion`,
                initialName: "龙娘对话",
                initialAttachMode: "chat",
                onClose: () => (creating.value = false),
                onStarted: () => kt.showOverlay("dragon"),
              }),
            )
          : null,
      ]);
    };
  },
});
