import { defineComponent, h, onUnmounted, ref, Teleport, watch } from "vue";
import {
  NewSessionDialog,
  openChat,
  useExtension,
} from "@kohakuterrarium/ui-sdk";
import { bridgeFor } from "./bridge.js";
import { settingsFor } from "./settings-state.js";
import { targetView } from "./target.js";

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
      if (await openChat(row, { signal: navigation.signal })) {
        store.update({ companion: row });
        kt.showOverlay("dragon");
        choices.value = [];
      }
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
    const launch = (replace = false) =>
      run(async (current) => {
        const response = await kt.api.get("companions");
        if (!current()) return;
        const rows = response.data.companions;
        const connected = store.state.value.companion;
        const existing =
          !replace &&
          connected &&
          rows.find((row) => row.creatureId === connected.creatureId);
        if (existing || (!replace && rows.length === 1 && !connected))
          await open(existing || rows[0]);
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
      const view = targetView(bridge, store.state.value);
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
          h(
            "dd",
            "固定龙娘对话，由同一个 Agent 回复并控制动作；切换页面不改变绑定。",
          ),
        ]),
        h("p", { class: "l2d-target-status" }, view.text),
        view.target
          ? h(
              "button",
              {
                type: "button",
                disabled: busy.value,
                onClick: () => run(() => bridge.openTarget()),
              },
              "打开此对话",
            )
          : null,
        view.controlledElsewhere || view.controllerUnavailable
          ? h("div", [
              h(
                "p",
                view.controllerUnavailable
                  ? "当前页面尚未连接宠物，可以在此页面接管。"
                  : "由另一页面控制，当前页面的会话选择不会影响宠物。",
              ),
              h(
                "button",
                { type: "button", onClick: () => bridge.ownership.takeover() },
                "在此页面接管",
              ),
            ])
          : null,
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
                {
                  type: "button",
                  disabled: busy.value,
                  onClick: () => launch(),
                },
                busy.value ? "正在打开…" : "创建 / 打开龙娘对话",
              ),
              h(
                "p",
                { class: "l2d-control-hint" },
                "选择后持续跟随这位龙娘；关闭聊天标签仍保留绑定。切换模式不会启动 Agent，也没有额外的后台 Agent。",
              ),
              store.state.value.companion
                ? h(
                    "button",
                    {
                      type: "button",
                      disabled: busy.value,
                      onClick: () => launch(true),
                    },
                    "更换对话",
                  )
                : null,
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
                onStarted: ({ id }) =>
                  run(async (current) => {
                    const { data } = await kt.api.get("companions");
                    if (!current()) return;
                    const row = data.companions.find(
                      (row) => row.sessionId === id || row.creatureId === id,
                    );
                    if (row) store.update({ companion: row });
                    kt.showOverlay("dragon");
                  }),
              }),
            )
          : null,
      ]);
    };
  },
});
