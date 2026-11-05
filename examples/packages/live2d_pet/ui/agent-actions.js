import { PetBehavior } from "./behavior.js";
import { playbackFor } from "./action-catalog.js";

export class AgentActions {
  constructor(automatic, model, catalog, now = () => performance.now()) {
    this.automatic = automatic;
    this.actor = new PetBehavior(now, model);
    this.model = model;
    this.setCatalog(catalog);
    this.now = now;
    this.enabled = false;
    this.seen = new Set();
    this.active = false;
  }
  setCatalog(catalog) {
    this.catalog = catalog;
    this.actions = catalog.actions;
    this.automatic.completedDuration =
      this.actions[catalog.defaults.ready].seconds * 1000;
    this.active = false;
    this.automaticState = null;
  }
  configure(enabled) {
    this.enabled = enabled === true;
    if (!this.enabled) this.release();
  }
  receive(command) {
    if (!command || typeof command.id !== "string" || this.seen.has(command.id))
      return false;
    this.seen.add(command.id);
    if (this.seen.size > 128) this.seen.delete(this.seen.values().next().value);
    if (command.operation === "release") {
      this.release();
      return true;
    }
    const definition =
      typeof command.action === "string" &&
      Object.hasOwn(this.actions, command.action)
        ? this.actions[command.action]
        : null;
    const remaining = command.expiresAt - Date.now();
    if (
      !this.enabled ||
      command.model !== this.model ||
      command.operation !== "perform" ||
      !definition ||
      !Number.isFinite(remaining) ||
      remaining <= 0 ||
      remaining > definition.maxSeconds * 1000 + 100 ||
      this.automatic.status === "waiting" ||
      this.automatic.waitingForInput
    )
      return false;
    this.actor.setWork(null);
    const state =
      definition.playback.type === "preset" ? definition.playback.name : "idle";
    this.actor.setWork({
      connected: true,
      hostId: "agent",
      sessionId: "agent",
      status: "idle",
      started: 0,
      completed: 0,
    });
    this.actor.setWork({
      connected: true,
      hostId: "agent",
      sessionId: "agent",
      status: state,
      started: 1,
      completed: state === "ready" ? 1 : 0,
    });
    this.definition = definition;
    this.startedAt = this.now();
    this.until = this.now() + remaining;
    this.active = true;
    this.releasing = false;
    return true;
  }
  release() {
    if (!this.active || this.releasing) return;
    this.actor.setWork({ ...this.actor.work, status: "idle" });
    this.releasing = true;
    this.until =
      this.now() +
      (this.definition.playback.type === "preset"
        ? this.actor.releaseDuration + 900
        : 0);
  }
  current(time) {
    if (this.automatic.status === "waiting" || this.automatic.waitingForInput) {
      this.active = false;
      return this.automatic;
    }
    if (this.active && !this.releasing && time >= this.until) this.release();
    if (this.active && this.releasing && time >= this.until)
      this.active = false;
    return this.active ? this.actor : this.automatic;
  }
  sample(time) {
    const actor = this.current(time);
    if (actor === this.actor)
      return this.definition.playback.type === "preset"
        ? actor.sample(time)
        : {};
    const selected = this.action(time);
    return actor.sample(time, {
      state: selected.state,
      workReactions: selected.state === actor.action(time).state,
    });
  }
  action(time) {
    const actor = this.current(time);
    if (actor === this.actor)
      return this.definition.playback.type === "preset"
        ? actor.action(time)
        : playbackFor(this.definition, 0, (time - this.startedAt) / 1000);
    const action = this.automatic.action(time);
    if (action.state !== this.automaticState) {
      this.automaticState = action.state;
      this.automaticSince = time;
    }
    if (action.state === "resolving") {
      const waiting = this.actions[this.catalog.defaults.waiting].playback;
      return waiting.type === "preset" && waiting.name === "waiting"
        ? action
        : { state: "idle" };
    }
    const definition = this.actions[this.catalog.defaults[action.state]];
    return definition
      ? playbackFor(
          definition,
          action.progress,
          (time - this.automaticSince) / 1000,
        )
      : action;
  }
  touch(region) {
    this.active = false;
    this.automatic.touch(region);
  }
}

export function connectAgentActions(
  player,
  automatic,
  interaction,
  actions,
  model,
) {
  const controller = new AgentActions(automatic, model, actions);
  const channel = new URL(location.href).searchParams.get("channel");
  const origin = document.referrer ? new URL(document.referrer).origin : null;
  const receive = (event) => {
    if (
      event.source !== parent ||
      event.origin !== origin ||
      event.data?.channel !== channel ||
      event.data?.type !== "kt-live2d-action"
    )
      return;
    if (
      player.paused ||
      (player.model.currentMotion === "Sleep" && !interaction.autoSleeping)
    ) {
      controller.release();
      return;
    }
    const command = event.data.command;
    if (controller.receive(command) && command.operation === "perform")
      interaction.activity();
  };
  window.addEventListener("message", receive);
  controller.dispose = () => window.removeEventListener("message", receive);
  return controller;
}
