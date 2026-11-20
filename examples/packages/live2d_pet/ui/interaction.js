import { modelAdapter } from "./models/index.js";
const defaults = modelAdapter().interaction;
export const STATE_KEY = defaults.key;
export const SLEEP_AFTER_MS = 60_000;
const expressions = defaults.expressions;

export function readSelection(storage, profile = {}) {
  try {
    const data = JSON.parse(storage.getItem(profile.key || STATE_KEY));
    if (!data || data.version !== 1) return null;
    return {
      motion:
        data.motion === "Sleep" && profile.sleep !== false ? "Sleep" : "Idle",
      expressions: Array.isArray(data.expressions)
        ? [
            ...new Set(
              data.expressions.filter((name) =>
                (profile.expressions || expressions).includes(name),
              ),
            ),
          ]
        : [],
    };
  } catch {
    return null;
  }
}

export class PetInteraction {
  constructor(
    model,
    {
      storage,
      mini = false,
      now = () => performance.now(),
      changed = () => {},
      profile = {},
      selection,
      onSave = () => {},
    } = {},
  ) {
    this.model = model;
    this.profile = { ...defaults, ...profile };
    this.storage = storage;
    this.onSave = onSave;
    this.autoSleep = true;
    this.mini = mini;
    this.now = now;
    this.changed = changed;
    this.lastActivity = now();
    this.autoSleeping = false;
    this.selection = selection ||
      readSelection(storage, profile) || {
        motion: "Idle",
        expressions: [],
      };
    this.restore();
  }

  applySelection(selection) {
    if (JSON.stringify(selection) === JSON.stringify(this.selection)) return;
    this.selection = {
      motion: selection.motion,
      expressions: [...selection.expressions],
    };
    this.autoSleeping = false;
    this.restore();
  }

  restore() {
    this.model.motion(this.selection.motion);
    this.applyExpressions(this.selection.expressions);
    this.changed();
  }

  applyExpressions(names) {
    for (const name of [...this.model.activeExpressions])
      if (!names.includes(name) && this.model.activeExpressions.has(name))
        this.model.expression(name);
    for (const name of names)
      if (!this.model.activeExpressions.has(name)) this.model.expression(name);
  }

  save() {
    this.selection = {
      motion: this.model.currentMotion,
      expressions: [...this.model.activeExpressions],
    };
    try {
      this.storage?.setItem(
        this.profile.key || STATE_KEY,
        JSON.stringify({ version: 1, ...this.selection }),
      );
    } catch {
      // The current frame remains interactive when storage is unavailable.
    }
    this.onSave(this.selection);
    this.changed();
  }

  activity() {
    this.lastActivity = this.now();
    if (this.autoSleeping) {
      this.autoSleeping = false;
      this.restore();
      return true;
    }
    return false;
  }

  motion(name) {
    this.activity();
    this.model.motion(name);
    this.save();
  }

  expression(name) {
    this.activity();
    this.model.workActions?.suspend();
    if (name) this.model.expression(name);
    else this.applyExpressions(this.appearanceStates());
    this.save();
  }

  appearanceStates() {
    return this.selection.expressions.filter((name) =>
      this.model.adapter.appearanceStates?.includes(name),
    );
  }

  cycle() {
    if (this.activity()) return;
    if (this.model.currentMotion === "Sleep") {
      this.motion("Idle");
      return;
    }
    const active = this.model.activeExpressions;
    const cycle = this.profile.cycle;
    const current = cycle.findIndex((name) => active.has(name));
    const next = cycle[current + 1];
    if (!this.profile.exclusiveCycle || !next)
      for (const name of cycle)
        if (active.has(name)) this.model.expression(name);
    if (next) this.model.expression(next);

    this.save();
  }

  tick({
    hidden = false,
    paused = false,
    menuOpen = false,
    working = false,
  } = {}) {
    if (working) {
      this.activity();
      return;
    }
    if (
      !this.autoSleep ||
      this.profile.sleep === false ||
      !this.mini ||
      hidden ||
      paused ||
      menuOpen
    ) {
      this.lastActivity = this.now();
      return;
    }
    if (
      this.autoSleeping ||
      this.model.currentMotion === "Sleep" ||
      this.now() - this.lastActivity < SLEEP_AFTER_MS
    )
      return;
    this.autoSleeping = true;
    this.applyExpressions(this.appearanceStates());
    this.model.motion("Sleep");
    this.changed();
  }
}

export function movedBeyondClick(start, point) {
  return Math.hypot(point.x - start.x, point.y - start.y) > 6;
}
