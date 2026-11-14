import { updatePreferences, PREFERENCES_KEY } from "./preferences.js";
import { readView, VIEW_KEY } from "./profiles.js";
import { models, modelAdapter } from "./models/index.js";

export function setupComparison(storage, mini, preferences) {
  const view = preferences
    ? { model: preferences.model, quality: preferences.quality }
    : readView(storage, location.search);
  const fish = view.model === "deepseek";
  try {
    storage?.setItem(VIEW_KEY, JSON.stringify(view));
  } catch {
    /* optional storage */
  }
  document.querySelector("#model-choices")?.replaceChildren(
    ...Object.values(models).map((adapter) => {
      const button = document.createElement("button");
      button.dataset.pet = adapter.id;
      button.textContent = adapter.name;
      return button;
    }),
  );
  for (const button of document.querySelectorAll(
    "[data-pet], [data-quality]",
  )) {
    const isPet = button.hasAttribute("data-pet");
    const value = isPet ? button.dataset.pet : button.dataset.quality;
    button.disabled = false;
    button.setAttribute(
      "aria-pressed",
      String(value === (isPet ? view.model : view.quality)),
    );
    button.addEventListener("click", () => {
      const next = { ...view, [isPet ? "model" : "quality"]: value };
      const url = new URL(location.href);
      url.searchParams.set("pet", next.model);
      url.searchParams.set("quality", next.quality);
      try {
        storage?.setItem(VIEW_KEY, JSON.stringify(next));
      } catch {
        /* optional storage */
      }
      if (window.parent !== window && document.referrer) {
        window.parent.postMessage(
          { type: "kt-live2d-navigate", ...next },
          new URL(document.referrer).origin,
        );
      } else {
        if (preferences) {
          const saved = updatePreferences(preferences, next);
          url.searchParams.set("preferences", JSON.stringify(saved));
          try {
            window.localStorage.setItem(PREFERENCES_KEY, JSON.stringify(saved));
          } catch {
            /* optional storage */
          }
        }
        location.replace(url.href);
      }
    });
  }
  modelAdapter(view.model).configurePreview(view.quality);
  const cameraKey = `kt-live2d-camera-${view.model}-${mini ? "mini" : "preview"}`;
  let saved;
  try {
    saved = JSON.parse(storage?.getItem(cameraKey));
  } catch {
    /* optional storage */
  }
  const camera =
    saved &&
    Number.isFinite(saved.zoom) &&
    Number.isFinite(saved.pan?.x) &&
    Number.isFinite(saved.pan?.y)
      ? { zoom: Math.max(0.5, Math.min(5, saved.zoom)), pan: saved.pan }
      : { zoom: 1, pan: { x: 0, y: 0 } };
  return {
    view,
    fish,
    camera,
    saveCamera(player) {
      if (!player) return;
      try {
        storage?.setItem(
          cameraKey,
          JSON.stringify({ zoom: player.zoom, pan: player.pan }),
        );
      } catch {
        /* optional storage */
      }
    },
  };
}
