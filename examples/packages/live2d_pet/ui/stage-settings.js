import {
  normalizePreferences,
  updatePreferences,
  MODEL_NAMES,
  IDLE_CHOICES,
  PREFERENCES_KEY,
} from "./preferences.js";

export function connectSettings(changed) {
  const url = new URL(location.href);
  const channel = url.searchParams.get("channel");
  const origin = document.referrer ? new URL(document.referrer).origin : null;
  let initial;
  try {
    initial = JSON.parse(
      url.searchParams.get("preferences") ||
        localStorage.getItem(PREFERENCES_KEY),
    );
  } catch {
    /* optional storage */
  }
  let preferences = normalizePreferences(
    initial || {
      version: 1,
      model: url.searchParams.get("pet"),
      quality: url.searchParams.get("quality"),
    },
  );
  let sequence = 0;
  const model = preferences.model;
  const loadedQuality = preferences.quality;
  const controls = document.createElement("div");
  controls.className = "pet-quick-settings";
  const select = (label, options, change) => {
    const row = document.createElement("label");
    row.append(label);
    const input = document.createElement("select");
    input.setAttribute("aria-label", label);
    for (const [value, name] of Object.entries(options)) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = name;
      input.append(option);
    }
    input.addEventListener("change", () => change(input.value));
    row.append(input);
    controls.append(row);
    return input;
  };
  const models = select("角色", MODEL_NAMES, (model) => patch({ model }));
  const quality = select(
    "画质",
    { high: "高品质 · 4K", standard: "性能优先 · 2K" },
    (quality) => patch({ quality }),
  );
  const idle = select("默认待机", IDLE_CHOICES[model], (idle) =>
    patch({ models: { [model]: { idle } } }),
  );
  const row = document.createElement("label");
  row.append("大小");
  const size = document.createElement("input");
  Object.assign(size, { type: "range", min: "50", max: "300", step: "5" });
  size.setAttribute("aria-label", "宠物大小");
  const output = document.createElement("output");
  let editingSize = false;
  const renderSize = () => {
    if (!editingSize)
      size.value = String(Math.round(preferences.models[model].scale * 100));
    output.textContent = `${size.value}%`;
  };
  const commitSize = () => {
    if (!editingSize) return;
    editingSize = false;
    patch({ models: { [model]: { scale: Number(size.value) / 100 } } });
  };
  size.addEventListener("input", () => {
    editingSize = true;
    renderSize();
  });
  size.addEventListener("change", commitSize);
  size.addEventListener("blur", commitSize);
  size.addEventListener("pointercancel", () => {
    editingSize = false;
    renderSize();
  });
  row.append(size, output);
  controls.append(row);
  const speechRow = document.createElement("label");
  speechRow.append("说话口型");
  const talkingMouth = document.createElement("input");
  talkingMouth.type = "checkbox";
  talkingMouth.setAttribute("aria-label", "说话口型");
  talkingMouth.addEventListener("change", () =>
    patch({ talkingMouth: talkingMouth.checked }),
  );
  speechRow.append(talkingMouth);
  controls.append(speechRow);
  document.querySelector("#pet-menu .pet-menu-actions").before(controls);

  function render() {
    models.value = preferences.model;
    quality.value = preferences.quality;
    idle.value = preferences.models[model].idle;
    talkingMouth.checked = preferences.talkingMouth;
    renderSize();
    document.body.classList.toggle("debug", preferences.debug);
    changed(preferences);
  }
  function patch(fields) {
    const next = updatePreferences(preferences, fields);
    if (JSON.stringify(next) === JSON.stringify(preferences)) return;
    preferences = next;
    if (channel && origin && parent !== window) {
      parent.postMessage(
        {
          type: "kt-live2d-preferences",
          channel,
          model,
          sequence: ++sequence,
          patch: fields,
        },
        origin,
      );
    } else {
      try {
        localStorage.setItem(PREFERENCES_KEY, JSON.stringify(next));
      } catch {
        /* retain in memory */
      }
      if (next.model !== model || next.quality !== loadedQuality) {
        url.searchParams.set("pet", next.model);
        url.searchParams.set("quality", next.quality);
        url.searchParams.set("preferences", JSON.stringify(next));
        location.replace(url.href);
        return;
      }
    }
    render();
  }
  function receive(event) {
    if (
      !channel ||
      !origin ||
      event.source !== parent ||
      event.origin !== origin ||
      event.data?.type !== "kt-live2d-settings" ||
      event.data.channel !== channel
    )
      return;
    const next = normalizePreferences(event.data.preferences);
    if (next.model !== model) return;
    preferences = next;
    render();
  }
  window.addEventListener("message", receive);
  return {
    get value() {
      return preferences;
    },
    patch,
    render,
    dispose() {
      window.removeEventListener("message", receive);
      controls.remove();
    },
  };
}
