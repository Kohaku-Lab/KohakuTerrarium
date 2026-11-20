import { models, modelAdapter } from "./models/index.js";
export {
  WORK_MOTIONS,
  fishExpressions,
  fishGroups,
  fishInteraction,
} from "./models/deepseek.js";
export const companionSize = (model) => [...modelAdapter(model).size];
export const VIEW_KEY = "kt-live2d-view-v1";

export function readView(storage, search = "") {
  let saved;
  try {
    saved = JSON.parse(storage?.getItem(VIEW_KEY));
  } catch {
    /* optional storage */
  }
  const query = new URLSearchParams(search);
  const model = query.get("pet") || saved?.model;
  const quality = query.get("quality") || saved?.quality;
  return {
    model: Object.hasOwn(models, model) ? model : "dragon",
    quality: quality === "standard" ? "standard" : "high",
  };
}

export function manifestPath(view, resourceBase) {
  const quality = view.quality === "standard" ? "standard" : "high";
  if (!modelAdapter(view.model).bundled) {
    if (!resourceBase)
      throw new Error("请在 KT 的 Live2D 设置中导入模型资源，再打开预览。");
    return `${resourceBase}${quality}.model3.json`;
  }
  return `./model/${quality}.model3.json`;
}
