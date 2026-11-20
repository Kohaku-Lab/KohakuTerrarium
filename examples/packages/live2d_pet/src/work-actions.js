import { modelAdapter } from "../ui/models/index.js";
import { CubismMotionQueueEntry } from "../vendor/Framework/src/motion/cubismmotionqueueentry";

export class WorkActions {
  constructor(profile) {
    this.profile = profile;
    this.adapter = modelAdapter(profile);
    this.clips = new Map();
    this.pose = new Map();
    this.state = "idle";
    this.elapsed = 0;
    this.suspended = 0;
    this.settling = 0;
  }

  load(owner, name, data) {
    const definition = JSON.parse(new TextDecoder().decode(data));
    const motion = owner.loadMotion(data, data.byteLength, name);
    motion.setLoop(false);
    motion.setFadeInTime(0);
    motion.setFadeOutTime(0);
    const ids = owner.getModel().getModel().parameters.ids;
    const parameters = definition.Curves.filter(
      (curve) => curve.Target === "Parameter",
    )
      .map((curve) => ({ name: curve.Id, index: ids.indexOf(curve.Id) }))
      .filter((parameter) => parameter.index >= 0);
    const entry = new CubismMotionQueueEntry();
    entry.setIsStarted(true);
    entry.setStartTime(0);
    entry.setFadeInStartTime(0);
    this.clips.set(name, {
      motion,
      parameters,
      entry,
      duration: definition.Meta.Duration,
    });
  }

  sampleClip(model, name, seconds) {
    const clip = this.clips.get(name);
    if (!clip) return {};
    const original = clip.parameters.map(({ index }) =>
      model.getParameterValueByIndex(index),
    );
    clip.entry.setIsFinished(false);
    clip.motion.doUpdateParameters(
      model,
      Math.min(seconds, clip.duration),
      1,
      clip.entry,
    );
    const result = {};
    clip.parameters.forEach(({ name, index }, i) => {
      result[name] = model.getParameterValueByIndex(index);
      model.setParameterValueByIndex(index, original[i]);
    });
    return result;
  }

  get transitioning() {
    if (this.sleeping) return false;
    return (
      this.elapsed < 1.5 ||
      this.state === "ready" ||
      this.suspended > 0 ||
      this.settling > 0
    );
  }

  refreshBounds() {
    this.settling = 1.5;
  }

  suspend() {
    this.suspended = 2;
  }

  apply(model, dt, action = { state: "idle" }, sleeping = false) {
    if (sleeping) {
      this.pose.clear();
      this.state = "idle";
      this.elapsed = 0;
      this.sleeping = true;
      return;
    }
    if (this.sleeping) {
      this.sleeping = false;
      this.previous = null;
    }
    this.settling = Math.max(0, this.settling - dt);
    const wasSuspended = this.suspended > 0;
    this.suspended = Math.max(0, this.suspended - dt);
    if (wasSuspended && !this.suspended) this.refreshBounds();
    const state = action.state || "idle";
    if (state === "resolving" && this.state !== "resolving") {
      this.exitFrom = {};
      const ids = model.getModel().parameters.ids;
      for (const name of this.adapter.exitParameters) {
        const index = ids.indexOf(name);
        if (index < 0) continue;
        const base = model.getParameterValueByIndex(index);
        const pose = this.pose.get(name);
        this.exitFrom[name] = pose
          ? base + (pose.value - base) * pose.weight
          : base;
      }
    }
    if (state !== this.state) {
      this.previous = this.state;
      this.state = state;
      this.elapsed = 0;
    }
    this.elapsed += dt;
    const target = this.suspended
      ? {}
      : this.adapter.workPose({
          state,
          t: this.elapsed,
          action,
          idleStyle: this.idleStyle,
          previous: this.previous,
          exitFrom: this.exitFrom,
          model,
          sample: (name, seconds) => this.sampleClip(model, name, seconds),
        });
    const ids = model.getModel().parameters.ids;
    const blend = 1 - Math.exp(-dt / 0.16);
    for (const name of new Set([...this.pose.keys(), ...Object.keys(target)])) {
      const index = ids.indexOf(name);
      if (index < 0) continue;
      const current = this.pose.get(name) || {
        value: model.getParameterValueByIndex(index),
        weight: 0,
      };
      const active = Object.hasOwn(target, name);
      if (
        state === "resolving" &&
        active &&
        this.adapter.exitParameters.includes(name)
      ) {
        current.value = target[name];
        current.weight = 1;
      } else {
        current.value +=
          ((active ? target[name] : current.value) - current.value) * blend;
        current.weight += ((active ? 1 : 0) - current.weight) * blend;
      }
      if (!active && current.weight < 0.001) {
        this.pose.delete(name);
        continue;
      }
      this.pose.set(name, current);
      model.setParameterValueByIndex(index, current.value, current.weight);
    }
  }

  release() {
    for (const { motion, entry } of this.clips.values()) {
      motion.release();
      entry.release();
    }
    this.clips.clear();
  }
}
