export function validateCapabilities(catalog, owner) {
  const ids = owner.getModel().getModel().parameters.ids;
  for (const [id, { playback: p }] of Object.entries(catalog.actions)) {
    const valid =
      p.type === "preset" ||
      (p.type === "pose" &&
        Object.keys(p.parameters).every(
          (name) =>
            ids.includes(name) &&
            !owner.appearanceParameters.has(ids.indexOf(name)),
        )) ||
      (p.type === "expression" &&
        owner.expressionDefinitions.has(p.name) &&
        !owner.adapter.appearanceStates?.includes(p.name)) ||
      (p.type === "motion" && owner.workActions.clips.has(p.name));
    if (!valid) throw new Error(`模型不支持动作 ${id} 的参数、表情或动画。`);
  }
}

export class ActionPose {
  constructor(owner) {
    this.owner = owner;
    this.values = new Map();
    this.transitioning = false;
  }
  apply(model, dt, action, sleeping) {
    if (sleeping) {
      this.values.clear();
      this.transitioning = false;
      return;
    }
    const p = action?.playback;
    this.transitioning =
      p?.type === "motion" &&
      (action.elapsed || 0) <
        (this.owner.workActions.clips.get(p.name)?.duration || 0);
    let target = {};
    if (p?.type === "pose") target = p.parameters;
    if (p?.type === "motion")
      target = this.owner.workActions.sampleClip(
        model,
        p.name,
        action.elapsed || 0,
      );
    const ids = model.getModel().parameters.ids;
    if (p?.type === "expression") {
      for (const parameter of this.owner.expressionDefinitions.get(p.name)
        ?.Parameters || []) {
        const index = ids.indexOf(parameter.Id);
        if (index < 0) continue;
        const base =
          target[parameter.Id] ?? model.getParameterValueByIndex(index);
        target[parameter.Id] =
          parameter.Blend === "Multiply"
            ? base * parameter.Value
            : parameter.Blend === "Overwrite"
              ? parameter.Value
              : base + parameter.Value;
      }
    }
    const blend = 1 - Math.exp(-dt / 0.12);
    for (const name of new Set([
      ...this.values.keys(),
      ...Object.keys(target),
    ])) {
      const index = ids.indexOf(name);
      if (index < 0) continue;
      const active = Object.hasOwn(target, name);
      const pose = this.values.get(name) || {
        value: model.getParameterValueByIndex(index),
        weight: 0,
      };
      if (
        Math.abs((active ? 1 : 0) - pose.weight) > 0.001 ||
        (active && Math.abs(target[name] - pose.value) > 0.01)
      )
        this.transitioning = true;
      if (active) pose.value += (target[name] - pose.value) * blend;
      pose.weight += ((active ? 1 : 0) - pose.weight) * blend;
      if (!active && pose.weight < 0.001) {
        this.values.delete(name);
        continue;
      }
      this.values.set(name, pose);
      const targets = this.owner.adapter.aliases[name]
        ? [name, this.owner.adapter.aliases[name]]
        : [name];
      for (const id of targets) {
        const i = ids.indexOf(id);
        if (i >= 0)
          model.setParameterValueByIndex(
            i,
            Math.max(
              model.getParameterMinimumValue(i),
              Math.min(model.getParameterMaximumValue(i), pose.value),
            ),
            pose.weight,
          );
      }
    }
  }
}
