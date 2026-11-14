import { companionSize } from "./profiles.js";
import {
  models,
  modelAdapter,
  normalizeExpressions,
  toggleExpression,
} from "./models/index.js";
export { DRAGON_EXPRESSIONS } from "./models/dragon.js";
import { savedActionSet } from "./action-catalog.js";

export const CONTROL_MODES = {
  pet: "普通 Pet",
  expression: "可选主动表达",
  agent: "专用 L2D Agent",
};

export const PREFERENCES_KEY = "companion-preferences-v1";
export const MODEL_NAMES = Object.fromEntries(
  Object.entries(models).map(([id, adapter]) => [id, adapter.name]),
);
export const IDLE_CHOICES = Object.fromEntries(
  Object.entries(models).map(([id, adapter]) => [id, adapter.idleChoices]),
);
export const expressionChoices = (model) => modelAdapter(model).expressions;

export function normalizePreferences(input) {
  const data = input?.version === 1 ? input : {};
  const models = {};
  for (const model of Object.keys(MODEL_NAMES)) {
    const source = data.models?.[model] || {};
    const expressions = normalizeExpressions(
      modelAdapter(model),
      Array.isArray(source.expressions) ? source.expressions : [],
    );
    models[model] = {
      resourceVersion: /^[a-f0-9]{32}$/.test(source.resourceVersion || "")
        ? source.resourceVersion
        : "",
      actionSet: source.actionSet
        ? savedActionSet(source.actionSet, model)
        : null,
      scale: Number.isFinite(source.scale)
        ? Math.min(3, Math.max(0.5, source.scale))
        : 1,
      idle: Object.hasOwn(IDLE_CHOICES[model], source.idle)
        ? source.idle
        : Object.keys(IDLE_CHOICES[model])[0],
      expressions,
    };
  }
  const controlMode = Object.hasOwn(CONTROL_MODES, data.controlMode)
    ? data.controlMode
    : data.agentControl === true
      ? "expression"
      : "pet";
  return {
    version: 1,
    model: Object.hasOwn(models, data.model) ? data.model : "dragon",
    quality: data.quality === "standard" ? "standard" : "high",
    debug: data.debug === true,
    controlMode,
    companion:
      data.companion &&
      typeof data.companion.creatureId === "string" &&
      typeof data.companion.sessionId === "string"
        ? {
            creatureId: data.companion.creatureId.slice(0, 200),
            sessionId: data.companion.sessionId.slice(0, 200),
            label: String(data.companion.label || "龙娘对话").slice(0, 200),
          }
        : null,
    agentControl: controlMode !== "pet",
    tracking: data.tracking !== false,
    physics: data.physics !== false,
    followWork: data.followWork !== false,
    talkingMouth: data.talkingMouth !== false,
    autoSleep: data.autoSleep !== false,
    models,
  };
}

export function updatePreferences(current, patch) {
  if (
    Object.hasOwn(patch, "agentControl") &&
    !Object.hasOwn(patch, "controlMode")
  )
    patch = {
      ...patch,
      controlMode: patch.agentControl === true ? "expression" : "pet",
    };
  return normalizePreferences({
    ...current,
    ...patch,
    version: 1,
    models: Object.fromEntries(
      Object.keys(MODEL_NAMES).map((model) => [
        model,
        { ...current.models[model], ...patch.models?.[model] },
      ]),
    ),
  });
}

export function toggleAppearance(preferences, model, name) {
  return toggleExpression(
    modelAdapter(model),
    preferences.models[model].expressions,
    name,
  );
}

export function selectionFor(preferences, model = preferences.model) {
  return {
    motion: preferences.models[model].idle === "sleep" ? "Sleep" : "Idle",
    expressions: preferences.models[model].expressions,
  };
}

export function fittedSize(preferences, width, height, aspect) {
  const base = companionSize(preferences.model);
  if (Number.isFinite(aspect) && aspect >= 0.2 && aspect <= 5) {
    base[0] = Math.min(base[0], base[1] * aspect);
    base[1] = base[0] / aspect;
  }
  const chrome = preferences.debug ? 80 : 0;
  const scale = Math.min(
    preferences.models[preferences.model].scale,
    Math.max(48, width - 16) / base[0],
    Math.max(48, height - 16 - chrome) / base[1],
  );
  return [
    Math.max(
      Math.min(preferences.debug ? 200 : 0, width - 16),
      Math.round(base[0] * scale),
    ),
    Math.round(base[1] * scale) + chrome,
  ];
}
