import { modelAdapter } from "./models/index.js";
export const WORK_LABELS = {
  idle: "陪你待机",
  running: "正在忙",
  waiting: "等你确认",
  ready: "有结果了",
  failed: "遇到问题了",
};
const poses = {
  idle: {},
  ready: {},
  running: { ParamAngleY: -6, ParamAngleZ: -3, ParamEyeBallY: -0.15 },
  waiting: { ParamAngleZ: 9, ParamBrowLY: 0.25, ParamBrowRY: 0.25 },
  failed: {
    ParamAngleY: -5,
    ParamBrowLY: -0.4,
    ParamBrowRY: -0.4,
    ParamMouthForm: -0.4,
  },
};

export class PetBehavior {
  constructor(now = () => performance.now(), profile = "dragon") {
    this.now = now;
    this.work = null;
    this.releaseDuration = modelAdapter(profile).releaseDuration;
    this.waitingSince = null;
    this.releaseAt = null;
    this.pose = {};
    this.lastSample = now();
    this.reaction = null;
    this.completedAt = null;
    this.completedDuration = 3400;
    this.lastTouch = -Infinity;
  }

  get status() {
    return this.work?.connected && WORK_LABELS[this.work.status]
      ? this.work.status
      : "idle";
  }

  get working() {
    return (
      this.work?.connected &&
      (this.work.busy || ["running", "waiting"].includes(this.status))
    );
  }

  setWork(work) {
    const old = this.work;
    const same =
      old &&
      work &&
      old.hostId === work.hostId &&
      old.sessionId === work.sessionId;
    const time = this.now();
    this.action(time);
    this.work = work;
    const reset =
      same && (work.started < old.started || work.completed < old.completed);
    if (!same || !old.connected || !work?.connected || reset) {
      this.reaction = null;
      this.completedAt = null;
      this.releaseAt = null;
      this.waitingSince = this.status === "waiting" ? time : null;
      return;
    }
    const newStart = work.started > old.started;
    if (
      ["running", "waiting", "failed"].includes(this.status) &&
      (old.status !== this.status || newStart)
    ) {
      this.reaction = null;
      this.completedAt = null;
    }
    if (this.status === "waiting") {
      this.waitingSince ??= time;
      this.releaseAt = null;
      return;
    }
    if (old.status === "waiting" && this.releaseAt === null) {
      this.releaseAt = Math.max(time + 250, (this.waitingSince ?? time) + 650);
    }
    const afterRelease =
      this.releaseAt === null ? time : this.releaseAt + this.releaseDuration;
    if (this.status === "running" && newStart && this.releaseAt === null)
      this.react("start");
    const completed = Number.isInteger(work.completed)
      ? work.completed > (old.completed ?? work.completed)
      : this.status === "ready" && old.status === "running" && !work.busy;
    if (completed && this.status === "ready") this.react("ready", afterRelease);
    if (this.status === "failed" && old.status !== "failed")
      this.react("failed", afterRelease);
  }

  action(time = this.now()) {
    if (this.releaseAt !== null) {
      const progress = (time - this.releaseAt) / this.releaseDuration;
      if (progress < 0) return { state: "waiting", progress: 0 };
      if (progress < 1) return { state: "resolving", progress };
      this.releaseAt = null;
      this.waitingSince = null;
    }
    const progress =
      this.completedAt === null
        ? 1
        : Math.max(0, (time - this.completedAt) / this.completedDuration);
    return {
      state:
        this.status === "ready"
          ? progress < 1
            ? "ready"
            : "idle"
          : this.status,
      progress,
    };
  }

  react(kind, at = this.now()) {
    if (kind === "ready") this.completedAt = at;
    this.reaction = { kind, at };
  }

  touch(region) {
    if (!["head", "body"].includes(region) || this.now() - this.lastTouch < 700)
      return false;
    this.lastTouch = this.now();
    this.react(region === "head" ? "pat" : "tap");
    return true;
  }

  sample(
    time = this.now(),
    { state = this.action(time).state, workReactions = true } = {},
  ) {
    const target = poses[state] || poses.idle;
    const blend = 1 - Math.exp(-Math.max(0, time - this.lastSample) / 220);
    this.lastSample = time;
    for (const key of new Set([
      ...Object.keys(this.pose),
      ...Object.keys(target),
    ]))
      this.pose[key] =
        (this.pose[key] || 0) +
        ((target[key] || 0) - (this.pose[key] || 0)) * blend;
    const result = { ...this.pose };
    const reaction = this.reaction;
    if (!reaction) return result;
    const t = (time - reaction.at) / 1800;
    if (t >= 1) {
      this.reaction = null;
      return result;
    }
    if (!workReactions && !["pat", "tap"].includes(reaction.kind))
      return result;
    if (t < 0) return result;
    const weight = Math.sin(Math.PI * Math.max(0, t)) ** 2;
    const add = (name, value) => {
      result[name] = (result[name] || 0) + value * weight;
    };
    if (["pat", "ready"].includes(reaction.kind)) {
      add("ParamAngleZ", reaction.kind === "pat" ? 12 : -5);
      add(
        "ParamAngleY",
        reaction.kind === "pat" ? 7 : -10 * Math.sin(t * Math.PI * 2),
      );
      add("ParamEyeLOpen", -0.65);
      add("ParamEyeROpen", -0.65);
      add("ParamEyeLSmile", 0.8);
      add("ParamEyeRSmile", 0.8);
      add("EyeSmile", 0.8);
      add("ParamMouthForm", 0.8);
    } else if (reaction.kind === "tap") {
      add("ParamAngleX", -10);
      add("ParamAngleZ", -7);
      add("ParamBrowLY", 0.5);
      add("ParamBrowRY", 0.5);
      add("ParamMouthOpenY", 0.25);
    } else if (reaction.kind === "start") {
      add("ParamAngleY", 12);
      add("ParamBrowLY", 0.3);
      add("ParamBrowRY", 0.3);
    } else if (reaction.kind === "failed") {
      add("ParamAngleZ", 8 * Math.sin(t * Math.PI * 2));
    }
    return result;
  }
}
