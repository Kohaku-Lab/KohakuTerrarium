export const DRAGON_EXPRESSIONS = {
  抬手: "侧举折扇",
  抬手2: "持扇掩面",
  开扇: "合起折扇",
  扇面: "切换扇面",
  变: "幼年态（小个子）",
};
export const dragon = {
  configurePreview() {},
  framingPan: 0,
  id: "dragon",
  name: "龙娘",
  bundled: false,
  size: [500, 671],
  fps: 60,
  releaseDuration: 800,
  idleChoices: { gentle: "轻柔待机", wings: "舒展翅膀", sleep: "闭眼打盹" },
  expressions: DRAGON_EXPRESSIONS,
  appearanceStates: ["变"],
  expressionGroups: [["抬手", "抬手2"]],
  interaction: {
    key: "kt-live2d-dragon-interaction-v1",
    expressions: Object.keys(DRAGON_EXPRESSIONS),
    cycle: ["抬手", "抬手2"],
    exclusiveCycle: true,
    sleep: true,
  },
  aliases: {
    ParamAngleX: "ParamAngleX1",
    ParamAngleY: "ParamAngleY1",
    ParamAngleZ: "ParamAngleZ1",
  },
  headParts: [
    "Part22",
    "Part11",
    "Part14",
    "Part20",
    "Part21",
    "Part15",
    "Part16",
  ],
  mouth: "ParamMouthOpenY",
  boundsParameter: "Param76",
  workMotions: {},
  exitParameters: ["Param36", "Param78", "Param80"],
  nod: {
    ParamAngleY: [0, 4, -28, 5, -20, 0],
    ParamBodyAngleY: [0, 0, -5, 0, -3, 0],
  },
  normalizeExpressions(names) {
    if (
      names.some((n) => ["开扇", "扇面"].includes(n)) &&
      !names.some((n) => ["抬手", "抬手2"].includes(n))
    )
      names.unshift("抬手");
    return names;
  },
  toggleExtras(active, name, removing) {
    if (removing && ["抬手", "抬手2"].includes(name)) {
      active.delete("开扇");
      active.delete("扇面");
    }
    if (name === "扇面") active.delete("开扇");
  },
  workPose({ state, t, action, idleStyle, exitFrom }) {
    let target;
    target = {
      Param76: -7 + Math.sin(t * 1.1),
      Param77: Math.sin(t * 1.1),
    };
    if (state === "idle" && idleStyle === "wings") target = {};
    if (state === "running") {
      target = {
        Param76: 1 + 8 * Math.sin(t * 2.4),
        Param77: 6 * Math.cos(t * 2.4),
        ParamBodyAngleZ: 2 * Math.sin(t * 1.2),
      };
    } else if (state === "waiting") {
      Object.assign(target, { Param78: 1, Param80: 0, Param36: 0 });
    } else if (state === "resolving") {
      const seconds = (action.progress || 0) * 0.8;
      Object.assign(target, {
        Param36:
          exitFrom.Param36 +
          (10 - exitFrom.Param36) * Math.min(1, seconds / 0.35),
        Param78:
          exitFrom.Param78 *
          (1 - Math.max(0, Math.min(1, (seconds - 0.35) / 0.45))),
        Param80: 0,
      });
    } else if (state === "ready") {
      const lift = Math.sin(
        Math.PI * Math.min(1, (action.progress || 0) / 0.85),
      );
      Object.assign(target, {
        Param76: 10,
        Param77: 8 * lift,
        ParamBodyAngleY: -3 * lift,
      });
    }
    return target;
  },
};
