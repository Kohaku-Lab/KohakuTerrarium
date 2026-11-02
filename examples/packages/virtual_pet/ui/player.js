const ACTIONS = {
  idle: { row: 0, holds: [1680, 660, 660, 840, 840, 1920] },
  running: { row: 7, holds: [120, 120, 120, 120, 120, 220] },
  "running-right": { row: 1, holds: [120, 120, 120, 120, 120, 120, 120, 220] },
  "running-left": { row: 2, holds: [120, 120, 120, 120, 120, 120, 120, 220] },
  waving: { row: 3, holds: [140, 140, 140, 280] },
  jumping: { row: 4, holds: [140, 140, 140, 140, 280] },
};
const PRIORITY = {
  jumping: 1,
  waving: 2,
  "running-left": 3,
  "running-right": 3,
};

function durationsFor(atlas, action) {
  const count = atlas.frames[action] || atlas.interactions?.[action] || 1;
  return ACTIONS[action].holds.slice(0, count);
}
function periodFor(atlas, action) {
  return durationsFor(atlas, action).reduce(
    (total, duration) => total + duration,
    0,
  );
}
export function cellFor(atlas, action, elapsed, still = false) {
  const name = ACTIONS[action] ? action : "idle";
  const durations = durationsFor(atlas, name);
  let column = 0;
  if (!still) {
    let phase = Math.max(0, elapsed) % periodFor(atlas, name);
    while (phase >= durations[column]) {
      phase -= durations[column];
      column += 1;
    }
  }
  return { row: ACTIONS[name].row, column };
}

export class PetPlayer {
  constructor(atlas) {
    this.atlas = atlas;
    this.work = null;
    this.feedbackStarted = null;
    this.feedbackDuration = periodFor(atlas, "running") * 3;
    this.started = 0;
    this.action = null;
    this.interaction = null;
    this.pressed = false;
    this.dragStarted = null;
    this.dragDirection = null;
    this.dragSuppressed = false;
  }
  cue(action, now) {
    if (!this.atlas.interactions?.[action] || this.pressed) return false;
    if (
      this.interaction &&
      now < this.interaction.until &&
      PRIORITY[action] <= PRIORITY[this.interaction.action]
    )
      return false;
    this.feedbackStarted = null;
    this.interaction = {
      action,
      start: now,
      until: now + periodFor(this.atlas, action) * 3,
    };
    return true;
  }
  press(now) {
    this.pressed = true;
    this.feedbackStarted = null;
    if (this.interaction?.action !== "waving" || now >= this.interaction.until)
      this.interaction = null;
  }
  beginDrag(now) {
    this.pressed = true;
    this.interaction = null;
    this.dragStarted = now;
    this.dragDirection = null;
    this.dragSuppressed = false;
  }
  direction(action, now) {
    if (this.dragStarted === null || this.dragDirection === action) return;
    this.dragDirection = action;
    this.interaction =
      this.atlas.interactions?.[action] && !this.dragSuppressed
        ? { action, start: now, until: now + periodFor(this.atlas, action) * 3 }
        : null;
  }
  release(now, click) {
    const wasDragging = this.dragStarted !== null;
    this.pressed = false;
    this.dragStarted = null;
    this.dragDirection = null;
    this.dragSuppressed = false;
    if (wasDragging) this.interaction = null;
    if (click) this.cue("waving", now);
  }
  cancel() {
    this.interaction = null;
    this.pressed = false;
    this.dragStarted = null;
    this.dragDirection = null;
    this.dragSuppressed = false;
    this.feedbackStarted = null;
  }
  setWork(work, now) {
    const sameSession =
      work &&
      this.work &&
      work.sessionId === this.work.sessionId &&
      work.hostId === this.work.hostId;
    if (!sameSession || !work.busy || work.started < this.work.started)
      this.feedbackStarted = null;
    else if (
      work.started > this.work.started &&
      !this.pressed &&
      (!this.interaction || now >= this.interaction.until)
    )
      this.feedbackStarted = now;
    this.work = work;
  }
  tick(now, still = false) {
    if (still) {
      this.interaction = null;
      this.feedbackStarted = null;
      if (this.dragStarted !== null) this.dragSuppressed = true;
    }
    if (this.interaction && now >= this.interaction.until)
      this.interaction = null;
    const cue = this.interaction;
    const deadline = this.feedbackStarted + this.feedbackDuration;
    const running =
      !still &&
      !this.pressed &&
      this.work?.busy &&
      this.work.status === "running" &&
      this.feedbackStarted !== null &&
      now < deadline;
    const action =
      cue && (!this.pressed || this.dragStarted !== null)
        ? cue.action
        : running
          ? "running"
          : "idle";
    if (action !== this.action) {
      this.started =
        this.feedbackStarted !== null && now >= deadline ? deadline : now;
      this.action = action;
    }
    return {
      ...cellFor(
        this.atlas,
        action,
        now -
          (cue && action === cue.action
            ? cue.start
            : running
              ? this.feedbackStarted
              : this.started),
        still,
      ),
      action,
    };
  }
}
