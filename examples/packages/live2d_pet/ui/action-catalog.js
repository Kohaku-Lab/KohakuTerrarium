import { models } from "./models/index.js";
const states = ["running", "waiting", "ready", "failed"];
const object = (value) =>
  value && typeof value === "object" && !Array.isArray(value);
const keys = (value, expected) =>
  object(value) &&
  Object.keys(value).sort().join() === [...expected].sort().join();
const string = (value, max) =>
  typeof value === "string" && value.length > 0 && value.length <= max;
const fail = (message) => {
  throw new Error(message);
};

export function validateActionSet(value, model) {
  if (
    !keys(value, ["version", "model", "label", "defaults", "actions"]) ||
    value.version !== 1
  )
    fail("动作集格式或版本不正确。");
  if (value.model !== model || !Object.hasOwn(models, model))
    fail("动作集与当前角色不匹配。");
  if (
    !string(value.label, 100) ||
    !object(value.actions) ||
    Object.keys(value.actions).length < 1 ||
    Object.keys(value.actions).length > 64
  )
    fail("动作集需要名称和 1–64 个动作。");
  if (
    !keys(value.defaults, states) ||
    Object.values(value.defaults).some(
      (id) => typeof id !== "string" || !Object.hasOwn(value.actions, id),
    )
  )
    fail("请为忙碌、等待、完成和失败指定有效的动作。");
  for (const [id, action] of Object.entries(value.actions)) {
    if (
      !/^[a-z][a-z0-9_-]{0,63}$/.test(id) ||
      !keys(action, ["description", "seconds", "maxSeconds", "playback"]) ||
      !string(action.description, 240)
    )
      fail("动作名称或说明不正确。");
    if (
      !Number.isFinite(action.seconds) ||
      !Number.isFinite(action.maxSeconds) ||
      !(
        1 <= action.seconds &&
        action.seconds <= action.maxSeconds &&
        action.maxSeconds <= 15
      )
    )
      fail("动作时长应为 1–15 秒，且不能超过该动作的上限。");
    const p = action.playback;
    if (
      !object(p) ||
      !["preset", "pose", "expression", "motion"].includes(p.type) ||
      !keys(p, ["type", p.type === "pose" ? "parameters" : "name"])
    )
      fail("动作应使用预设、参数姿态、表情或动画。");
    if (p.type === "pose") {
      if (
        !object(p.parameters) ||
        Object.keys(p.parameters).length < 1 ||
        Object.keys(p.parameters).length > 32 ||
        Object.entries(p.parameters).some(
          ([id, n]) =>
            !string(id, 100) || !Number.isFinite(n) || Math.abs(n) > 1000,
        )
      )
        fail("参数姿态需要 1–32 个有效参数，数值范围为 -1000 至 1000。");
    } else if (
      !string(p.name, 100) ||
      (p.type === "preset" && !states.includes(p.name))
    )
      fail("找不到有效的动作能力名称。");
  }
  return JSON.parse(JSON.stringify(value));
}

export function savedActionSet(value, model) {
  try {
    return validateActionSet(value, model);
  } catch {
    return null;
  }
}

export function playbackFor(definition, progress = 0, elapsed = 0) {
  const playback = definition?.playback;
  return playback?.type === "preset"
    ? { state: playback.name, progress }
    : { state: "idle", playback, elapsed };
}

export function configureActionCatalog(
  controller,
  owner,
  custom,
  fallback,
  enabled,
) {
  let catalog,
    error = "";
  try {
    catalog = validateActionSet(custom || fallback, owner.profile);
    owner.validateActionSet(catalog);
  } catch (failure) {
    catalog = fallback;
    error = failure.message;
  }
  if (JSON.stringify(catalog) !== JSON.stringify(controller.catalog))
    controller.setCatalog(catalog);
  controller.configure(enabled && !error);
  return error;
}
