export const WORK_MOTIONS = {
  FingerIn: "Animation/竖手指切出来动画.motion3.json",
  FingerOut: "Animation/竖手指切回默认.motion3.json",
  RaiseIn: "Animation/默认手切举手.motion3.json",
  RaiseOut: "Animation/举手切默认手.motion3.json",
};

export const fishExpressions = [
  "脸红",
  "流泪",
  "爱心眼",
  "圈圈眼",
  "屑屑眼",
  "＞＜",
  "流冷汗",
  "黑脸",
  "加载",
  "兔牙",
  "单片眼镜",
  "黑框眼镜",
  "钢盆",
  "体型1",
  "体型2",
  "默认体型",
  "前倾",
  "尾巴开关",
  "裙子切换",
  "脱鞋",
  "胸口衣服",
];
export const fishGroups = [
  ["爱心眼", "圈圈眼", "屑屑眼", "＞＜"],
  ["体型1", "体型2", "默认体型"],
  ["单片眼镜", "黑框眼镜"],
];
export const fishInteraction = {
  key: "kt-live2d-deepseek-interaction-v1",
  expressions: fishExpressions,
  cycle: ["脸红", "爱心眼"],
  exclusiveCycle: false,
  sleep: false,
};

export const deepseek = {
  configurePreview(quality) {
    document.title = "大肥鱼 · Live2D 画质比较";
    document.querySelector("h1").replaceChildren("大肥鱼的小天地");
    const subtitle = document.createElement("span");
    subtitle.textContent =
      quality === "high" ? "高清档 · 最大边 4096" : "日常档 · 最大边 2048";
    document.querySelector("h1").append(subtitle);
    document
      .querySelector(".viewport")
      .setAttribute("aria-label", "大肥鱼模型预览");
    document
      .querySelector("#model")
      .setAttribute("aria-label", "可互动的大肥鱼 Live2D 模型");
    document.querySelector("aside header .eyebrow").textContent =
      "DEEPSEEK COMPANION";
    document.querySelector("aside header p").textContent =
      "放大后切换画质，比较眼睛、发丝与衣服细线。";
    document.querySelector("[data-motion=Idle]").textContent = "自然待机";
    document.querySelector("[data-motion=Sleep]").remove();
    const buttons = document.querySelector(".expressions");
    buttons.replaceChildren();
    for (const name of [...fishExpressions, ""]) {
      const button = document.createElement("button");
      button.dataset.expression = name;
      button.textContent = name || "恢复表情";
      buttons.append(button);
    }
    document.querySelector(".note").textContent =
      "再点一次关闭。眼型、眼镜和体型分别互斥；其他表情可叠加。";
    document
      .querySelector("#pet-menu-button")
      .setAttribute("aria-label", "大肥鱼动作菜单");
    document
      .querySelector("#pet-menu")
      .setAttribute("aria-label", "大肥鱼动作");
    document.querySelector("#pet-menu strong").textContent = "大肥鱼表情";
    document.querySelector("#pet-menu p").textContent =
      "摸头或点击身体会回应；右键选择固定表情。";
    document.querySelector("#credits").textContent =
      "大肥鱼 · 原画及拆分：神宫凉子 · Live2D：霓虹猫_NeonNeko · 神宫凉子免费配布模型";
  },
  framingPan: -1,
  id: "deepseek",
  name: "大肥鱼 DeepSeek",
  shortName: "大肥鱼",
  bundled: false,
  size: [384, 516],
  fps: 30,
  releaseDuration: 600,
  idleChoices: { natural: "自然待机" },
  expressions: Object.fromEntries(fishExpressions.map((name) => [name, name])),
  appearanceStates: [
    "兔牙",
    "单片眼镜",
    "黑框眼镜",
    "钢盆",
    "体型1",
    "体型2",
    "默认体型",
    "尾巴开关",
    "裙子切换",
    "脱鞋",
    "胸口衣服",
  ],
  expressionGroups: fishGroups,
  interaction: fishInteraction,
  aliases: {},
  headParts: [
    "Part15",
    "Part154",
    "Part2",
    "Part9",
    "Part111",
    "Part34",
    "Part155",
  ],
  mouth: "ParamMouthOpenY",
  boundsParameter: null,
  workMotions: WORK_MOTIONS,
  exitParameters: ["LeanInput"],
  nod: {
    ParamAngleY: [0, 3, -20, 4, -14, 0],
    ParamBodyAngleY: [0, 0, -2, 0, -1, 0],
  },
  normalizeExpressions: (names) => names,
  toggleExtras() {},
  workPose({ state, t, action, previous, exitFrom, model, sample }) {
    let target = {};

    if (state === "running") {
      target = sample("FingerIn", t);
      target.ParamBodyAngleZ = 5 * Math.sin(t * 1.6);
    } else if (state === "waiting") {
      target.LeanInput = -30;
    } else if (state === "resolving") {
      const progress = Math.max(0, Math.min(1, action.progress || 0));
      const ease = progress * progress * (3 - 2 * progress);
      const index = model.getModel().parameters.ids.indexOf("LeanInput");
      target.LeanInput =
        exitFrom.LeanInput +
        (model.getParameterValueByIndex(index) - exitFrom.LeanInput) * ease;
    } else if (state === "ready") {
      const seconds = (action.progress || 0) * 3.4;
      target =
        seconds < 2.3
          ? sample("RaiseIn", seconds)
          : sample("RaiseOut", seconds - 2.3);
    }
    if (state !== "running" && previous === "running" && t < 1.25)
      target = { ...sample("FingerOut", t), ...target };
    return target;
  },
};
