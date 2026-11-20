import { ref } from "vue";
import {
  normalizePreferences,
  PREFERENCES_KEY,
  updatePreferences,
} from "./preferences.js";
import { readSelection } from "./interaction.js";
import { readView } from "./profiles.js";
import { models as adapters } from "./models/index.js";

const stores = new WeakMap();

export function createSettingsStore(storage, legacy) {
  let saved;
  try {
    saved = storage.get(PREFERENCES_KEY);
  } catch {
    /* optional storage */
  }
  if (!saved) {
    const view = readView(legacy);
    const models = {};
    for (const [model, adapter] of Object.entries(adapters)) {
      const selection = readSelection(legacy, adapter.interaction);
      models[model] = {
        expressions: selection?.expressions || [],
        idle:
          selection?.motion === "Sleep"
            ? "sleep"
            : Object.keys(adapter.idleChoices)[0],
      };
    }
    saved = { version: 1, ...view, models };
  }
  const state = ref(normalizePreferences(saved));
  function persist() {
    try {
      storage.set(PREFERENCES_KEY, state.value);
    } catch {
      /* retain in memory */
    }
  }
  persist();
  return {
    state,
    update(patch) {
      const next = updatePreferences(state.value, patch);
      if (JSON.stringify(next) === JSON.stringify(state.value)) return;
      state.value = next;
      persist();
    },
    reset(model) {
      const defaults = normalizePreferences(null);
      this.update({ models: { [model]: defaults.models[model] } });
    },
  };
}

export function settingsFor(kt) {
  const storage = kt.storage;
  if (!stores.has(storage)) {
    let legacy;
    try {
      legacy = window.sessionStorage;
    } catch {
      /* optional migration */
    }
    let initializing = true;
    const store = createSettingsStore(
      {
        get: (...args) => storage.get(...args),
        set: (key, value) => {
          storage.set(key, value);
          // Do not overwrite a backend preference with defaults after a failed load.
          if (!initializing)
            kt.preferences?.set(key, JSON.parse(JSON.stringify(value)));
        },
      },
      legacy,
    );
    initializing = false;
    stores.set(storage, store);
  }
  return stores.get(storage);
}

export async function initializeSettings(kt) {
  try {
    const saved = await kt.preferences?.get(PREFERENCES_KEY);
    if (saved) kt.storage.set(PREFERENCES_KEY, saved);
  } catch {
    // Existing local choices still work when the backend is unavailable.
  }
  return settingsFor(kt);
}
