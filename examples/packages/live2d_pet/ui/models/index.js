import { dragon } from "./dragon.js";
import { deepseek } from "./deepseek.js";
export const models = { dragon, deepseek };
export function modelAdapter(id = "dragon") {
  const adapter = models[id];
  if (!adapter) throw new Error(`Unsupported Live2D adapter: ${id}`);
  return adapter;
}
export function normalizeExpressions(adapter, names) {
  let result = [
    ...new Set(
      names.filter((name) => Object.hasOwn(adapter.expressions, name)),
    ),
  ];
  for (const group of adapter.expressionGroups) {
    const last = result.filter((name) => group.includes(name)).at(-1);
    result = result.filter((name) => !group.includes(name) || name === last);
  }
  return adapter.normalizeExpressions(result);
}
export function toggleExpression(adapter, names, name) {
  if (!name) return [];
  if (!Object.hasOwn(adapter.expressions, name)) return [...names];
  const active = new Set(names);
  const removing = active.delete(name);
  if (!removing) {
    for (const group of adapter.expressionGroups)
      if (group.includes(name)) for (const other of group) active.delete(other);
    active.add(name);
  }
  adapter.toggleExtras(active, name, removing);
  return normalizeExpressions(adapter, [...active]);
}
