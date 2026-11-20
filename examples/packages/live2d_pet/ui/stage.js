import { connectRendererHealth } from "./renderer-health.js";
import { connectAgentActions } from "./agent-actions.js";
import { validateActionSet, configureActionCatalog } from "./action-catalog.js";
import { connectSettings } from "./stage-settings.js?v=desktop-actions-1";
import { selectionFor } from "./preferences.js";
import { PetInteraction } from "./interaction.js?v=desktop-actions-1";
import { setupComparison } from "./comparison.js?v=desktop-actions-1";
import { manifestPath } from "./profiles.js";
import { modelAdapter } from "./models/index.js";
import { importedResources } from "./resource-loader.js";
import { connectMenuPosition } from "./menu-position.js";

import { PetBehavior } from "./behavior.js?v=desktop-actions-1";
import { connectWork } from "./work.js?v=desktop-actions-1";
import { bindGestures } from "./gestures.js?v=desktop-actions-1";
import { connectCrop } from "./crop.js?v=desktop-actions-1";

let disconnectWork, disconnectGestures, agentActions;
const status = document.querySelector("#status");
const controls = document.querySelector("#controls");
const canvas = document.querySelector("#model");
const mini = new URL(location.href).searchParams.has("mini");
let storage;
try {
  storage = window.sessionStorage;
} catch {
  /* optional storage */
}
const preferences = connectSettings(applySettings);
const comparison = setupComparison(storage, mini, preferences.value);
const behavior = new PetBehavior(
  () => performance.now(),
  comparison.view.model,
);
document.body.classList.toggle("mini", mini);
document.documentElement.classList.toggle("mini", mini);
const menu = document.querySelector("#pet-menu");
const menuPosition = connectMenuPosition(menu);
window.addEventListener("pagehide", () => menuPosition.dispose(), {
  once: true,
});
const menuButton = document.querySelector("#pet-menu-button");
const closeButton = document.querySelector("#pet-close");
const hostChannel = new URL(location.href).searchParams.get("channel");
closeButton.hidden = !(
  mini &&
  hostChannel &&
  document.referrer &&
  parent !== window
);
closeButton.parentElement.hidden = closeButton.hidden;
closeButton.addEventListener("click", () => {
  if (closeButton.hidden) return;
  closeMenu();
  parent.postMessage(
    { type: "kt-live2d-close", channel: hostChannel },
    new URL(document.referrer).origin,
  );
});
let player, interaction, idleTimer, defaultCatalog, resources;
const health = connectRendererHealth(() => player);
const crop = mini ? connectCrop(() => player) : null;

function applySettings(settings) {
  if (
    !player?.model ||
    !interaction ||
    settings.model !== comparison.view.model
  )
    return;
  if (agentActions) {
    const custom = settings.models[settings.model].actionSet;
    const key = JSON.stringify(custom);
    const error = configureActionCatalog(
      agentActions,
      player.model,
      custom,
      defaultCatalog,
      settings.agentControl,
    );
    const channel = new URL(location.href).searchParams.get("channel");
    if (channel && document.referrer && parent !== window)
      parent.postMessage(
        {
          type: "kt-live2d-actions-ready",
          channel,
          catalogKey: key,
          error,
        },
        new URL(document.referrer).origin,
      );
  }
  interaction.autoSleep = settings.autoSleep;
  if (!settings.autoSleep) interaction.activity();
  interaction.applySelection(selectionFor(settings));
  player.model.workActions.idleStyle = settings.models[settings.model].idle;
  player.diagnostics = settings.debug;
  player.tracking = settings.tracking;
  player.physics = settings.physics;
  document.querySelector("#tracking").checked = settings.tracking;
  document.querySelector("#physics").checked = settings.physics;
  disconnectWork?.configure(settings);
  if (!settings.debug) {
    player.paused = false;
    document.querySelector("#pause").checked = false;
  }
}

function syncControls() {
  document
    .querySelectorAll("[data-motion]")
    .forEach((button) =>
      button.setAttribute(
        "aria-pressed",
        String(player.model.currentMotion === button.dataset.motion),
      ),
    );
  document
    .querySelectorAll("[data-expression]")
    .forEach((button) =>
      button.setAttribute(
        "aria-pressed",
        String(player.model.activeExpressions.has(button.dataset.expression)),
      ),
    );
  document.querySelector("#mini-hint").textContent = interaction?.autoSleeping
    ? "打盹中 · 移入唤醒"
    : player.model.currentMotion === "Sleep"
      ? "打盹中 · 点击唤醒"
      : "摸摸头 · 点击身体 · 右键选动作";
}

const report = (message, data = {}) => {
  status.textContent = message;
  status.classList.toggle("error", Boolean(data.error));
  if (data.error) {
    health.fail(message);
    controls.disabled = true;
    menuButton.disabled = true;
    closeMenu();
    clearInterval(idleTimer);
  }
  if (data.fps !== undefined) {
    controls.disabled = false;
    menuButton.disabled = false;
    document.body.classList.add("ready");
    if (preferences.value.debug) {
      const stats = data.stats;
      const summary = `${data.fps} FPS · ${preferences.value.quality === "high" ? "4K" : "2K"} · 纹理 ${player.textureBytes / 1048576} MiB`;
      const cost = stats
        ? ` · CPU帧 ${stats.cpuMean.toFixed(1)}/${stats.cpuP95.toFixed(1)} ms · >100ms ${stats.stalls} · ${stats.seconds.toFixed(0)}s`
        : "";
      const mouth = data.mouthRange
        ? ` · 口型 ${data.mouthRange.join("–")}%`
        : "";
      document.querySelector("#metrics").textContent = summary + cost + mouth;
      document.querySelector("#mini-metrics").textContent =
        summary + cost + mouth;
    }
  }
};

function closeMenu() {
  menuPosition.cancel();
  menu.hidden = true;
  menuButton.setAttribute("aria-expanded", "false");
}
document.querySelector("#pet-menu-close").addEventListener("click", closeMenu);

function openMenu(x, y) {
  if (!interaction || player.dead) return;
  interaction.activity();
  menu.style.visibility = "hidden";
  menu.hidden = false;
  menuButton.setAttribute("aria-expanded", "true");
  void menuPosition.position(x, y).then(() => {
    if (!menu.hidden)
      menu.querySelector("button").focus({ preventScroll: true });
  });
}

const appearanceStates =
  modelAdapter(comparison.view.model).appearanceStates || [];
const expressionButtons = document.querySelector("#controls .expressions");
const appearanceSection = document.createElement("section");
const appearanceHeading = document.createElement("h3");
appearanceHeading.textContent = "形象状态";
const appearanceButtons = document.createElement("div");
appearanceButtons.className = "buttons";
appearanceSection.append(appearanceHeading, appearanceButtons);
expressionButtons.parentElement.before(appearanceSection);
expressionButtons.parentElement.querySelector("h3").textContent = "姿势与表情";
for (const button of expressionButtons.querySelectorAll("[data-expression]")) {
  if (appearanceStates.includes(button.dataset.expression))
    appearanceButtons.append(button);
  else if (!button.dataset.expression) button.textContent = "恢复姿势与表情";
}
const menuActions = menu.querySelector(".pet-menu-actions");
const menuHeading = (label) => {
  const heading = document.createElement("h3");
  heading.textContent = label;
  menuActions.append(heading);
};
menuHeading("动作与表情");
const appearanceClones = [];
for (const source of document.querySelectorAll(
  "#controls [data-motion], #controls [data-expression], #controls [data-demo-state]",
)) {
  const button = source.cloneNode(true);
  if (source.hasAttribute("data-demo-state"))
    button.classList.add("debug-only");
  button.removeAttribute("title");
  if (appearanceStates.includes(source.dataset.expression))
    appearanceClones.push(button);
  else menuActions.append(button);
}
menuHeading("形象状态");
menuActions.append(...appearanceClones);

try {
  if (!globalThis.Live2DCubismCore)
    throw new Error("未找到 Cubism Core，请先准备本地 SDK 资源。");
  const { DragonPlayer } =
    await import("./runtime/player.js?v=desktop-actions-1");
  player = new DragonPlayer(canvas, report);
  player.afterDraw = () => crop?.update();
  let resourceBase;
  if (!modelAdapter(comparison.view.model).bundled) {
    const version = new URL(location.href).searchParams.get("resourceVersion");
    if (
      !/^[a-f0-9]{32}$/.test(version || "") ||
      !hostChannel ||
      !document.referrer ||
      parent === window
    )
      throw new Error("请在 KT 的 Live2D 设置中导入模型资源，再打开预览。");
    resourceBase = new URL(`./imported/${version}/`, location.href).href;
    resources = importedResources(
      resourceBase,
      hostChannel,
      new URL(document.referrer).origin,
    );
    player.resourceBytes = resources.bytes;
  }
  await player.load(
    new URL(manifestPath(comparison.view, resourceBase), location.href).href,
    new URL("./runtime/shaders/", location.href).href,
  );
  if (mini) player.targetFps = 30;
  player.zoom = mini ? 1 : comparison.camera.zoom;
  player.pan = mini ? { x: 0, y: 0 } : comparison.camera.pan;
  document.querySelector("#zoom").value = player.zoom;
  document.querySelector("#zoom-value").textContent =
    `${Math.round(player.zoom * 100)}%`;
  interaction = new PetInteraction(player.model, {
    storage,
    mini,
    changed: syncControls,
    profile: modelAdapter(comparison.view.model).interaction,
    selection: selectionFor(preferences.value, comparison.view.model),
    onSave: (selection) => {
      const model = comparison.view.model;
      const previous = preferences.value.models[model].idle;
      const idle =
        selection.motion === "Sleep"
          ? "sleep"
          : previous === "sleep"
            ? "gentle"
            : previous;
      preferences.patch({
        models: { [model]: { idle, expressions: selection.expressions } },
      });
    },
  });
  const setsResponse = await fetch(
    new URL("./action-sets.json", location.href),
  );
  if (!setsResponse.ok) throw new Error("动作集加载失败");
  const sets = await setsResponse.json();
  defaultCatalog = validateActionSet(
    sets.sets[comparison.view.model],
    comparison.view.model,
  );
  agentActions = connectAgentActions(
    player,
    behavior,
    interaction,
    defaultCatalog,
    comparison.view.model,
  );
  player.behavior = agentActions;
  disconnectWork = connectWork(behavior, interaction, comparison.view.model, {
    ...preferences.value,
    speech: player.speech,
    onSpeech: () => {
      interaction.activity();
      player.speech.suspend(
        document.hidden ||
          player.paused ||
          player.model.currentMotion === "Sleep",
      );
    },
    aspect:
      (player.bounds.right - player.bounds.left) /
      (player.bounds.top - player.bounds.bottom),
  });
  preferences.render();
  disconnectGestures = bindGestures(canvas, player, interaction, agentActions, {
    mini,
    closeMenu,
    isMenuOpen: () => !menu.hidden,
    movePet: (event) => {
      const channel = new URL(location.href).searchParams.get("channel");
      if (channel && document.referrer && parent !== window)
        parent.postMessage(
          { type: "kt-live2d-drag", channel, ...event },
          new URL(document.referrer).origin,
        );
    },
  });
  syncControls();
  idleTimer = setInterval(
    () =>
      interaction.tick({
        hidden: document.hidden,
        paused: player.paused,
        menuOpen: !menu.hidden,
        working: behavior.working || agentActions?.active,
      }),
    1000,
  );
} catch (error) {
  report(error.message, { error: true });
  player?.dispose();
}

for (const button of document.querySelectorAll(
  "[data-motion], [data-expression]",
)) {
  button.addEventListener("click", () => {
    if (!interaction || player.dead) return;
    agentActions?.release();
    if (button.hasAttribute("data-motion"))
      interaction.motion(button.dataset.motion);
    else interaction.expression(button.dataset.expression);
    if (menu.contains(button)) {
      closeMenu();
      menuButton.focus();
    }
  });
}
for (const button of document.querySelectorAll("[data-demo-state]")) {
  button.addEventListener("click", () => {
    if (!interaction || player.dead) return;
    interaction.activity();
    disconnectWork.preview(button.dataset.demoState);
    closeMenu();
  });
}
menuButton.addEventListener("click", () => {
  if (menu.hidden) openMenu(innerWidth, 36);
  else closeMenu();
});
canvas.addEventListener("contextmenu", (event) => {
  event.preventDefault();
  openMenu(event.clientX, event.clientY);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeMenu();
    menuButton.focus();
  }
});
for (const [id, property] of [
  ["tracking", "tracking"],
  ["physics", "physics"],
  ["pause", "paused"],
]) {
  document.querySelector(`#${id}`).addEventListener("change", (event) => {
    interaction.activity();
    player[property] = event.target.checked;
    if (property === "tracking" || property === "physics")
      preferences.patch({ [property]: event.target.checked });
  });
}
document.querySelector("#zoom").addEventListener("input", (event) => {
  interaction.activity();
  player.zoom = Number(event.target.value);
  document.querySelector("#zoom-value").textContent =
    `${Math.round(player.zoom * 100)}%`;
});
document.querySelector("#reset").addEventListener("click", () => {
  player.zoom = 1;
  player.pan = { x: 0, y: 0 };
  document.querySelector("#zoom").value = 1;
  document.querySelector("#zoom-value").textContent = "100%";
});
for (const button of document.querySelectorAll("[data-framing]")) {
  button.addEventListener("click", () => {
    interaction.activity();
    player.zoom = Number(button.dataset.framing);
    player.pan = {
      x: 0,
      y: modelAdapter(comparison.view.model).framingPan * (player.zoom - 1),
    };
    document.querySelector("#zoom").value = player.zoom;
    document.querySelector("#zoom-value").textContent =
      `${Math.round(player.zoom * 100)}%`;
  });
}
document.querySelector("#background").addEventListener("click", (event) => {
  const dark = document.body.classList.toggle("dark");
  event.target.textContent = dark ? "切换浅色背景" : "切换深色背景";
});
document.addEventListener("visibilitychange", () => {
  player?.speech.suspend(
    document.hidden || player.paused || player.model?.currentMotion === "Sleep",
  );
  if (!document.hidden) interaction?.activity();
});
canvas.addEventListener("webglcontextlost", (event) => {
  event.preventDefault();
  if (!player?.dead) {
    report("画面连接已中断，请重新打开预览。", { error: true });
    player?.dispose();
  }
});
window.addEventListener("pagehide", () => {
  health.dispose();
  preferences.dispose();
  resources?.dispose();
  crop?.dispose();
  disconnectWork?.();
  disconnectGestures?.();
  agentActions?.dispose();
  comparison.saveCamera(player);
  clearInterval(idleTimer);
  player?.dispose();
});
