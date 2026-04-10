/**
 * Keyboard shortcut composable tests.
 *
 * Vue's composable APIs (onMounted/onUnmounted) need a component
 * context, so we mount a tiny harness component that calls the
 * composable, then dispatch synthetic KeyboardEvents on window.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { defineComponent, h } from "vue";

import { useKeyboardShortcuts } from "./useKeyboardShortcuts.js";
import { useLayoutStore } from "@/stores/layout.js";
import { LAYOUT_EVENTS } from "@/utils/layoutEvents.js";

function makePreset(id) {
  return {
    id,
    label: id,
    zones: { main: { visible: true, size: 100 } },
    slots: [{ zoneId: "main", panelId: "chat" }],
  };
}

function setupStoreWithPresets() {
  const store = useLayoutStore();
  for (const id of [
    "chat-focus",
    "workspace",
    "multi-creature",
    "canvas",
    "debug",
    "settings",
  ]) {
    store.registerBuiltinPreset(makePreset(id));
  }
  store.switchPreset("chat-focus");
  return store;
}

const Harness = defineComponent({
  setup() {
    useKeyboardShortcuts();
    return () => h("div");
  },
});

let wrapper;

beforeEach(() => {
  setActivePinia(createPinia());
  localStorage.clear();
});

afterEach(() => {
  if (wrapper) {
    wrapper.unmount();
    wrapper = null;
  }
  document.body.innerHTML = "";
});

function mountHarness() {
  wrapper = mount(Harness);
  return wrapper;
}

function press(key, modifiers = {}) {
  const e = new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
    ctrlKey: modifiers.ctrl ?? false,
    shiftKey: modifiers.shift ?? false,
    metaKey: modifiers.meta ?? false,
  });
  // Allow passing a custom target for "editable focused" tests.
  if (modifiers.target) {
    Object.defineProperty(e, "target", { value: modifiers.target });
  }
  window.dispatchEvent(e);
  return e;
}

describe("useKeyboardShortcuts", () => {
  it("Ctrl+1 switches to the first preset", () => {
    const store = setupStoreWithPresets();
    mountHarness();
    press("2", { ctrl: true });
    expect(store.activePresetId).toBe("workspace");
    press("5", { ctrl: true });
    expect(store.activePresetId).toBe("debug");
  });

  it("bare digit without ctrl does nothing", () => {
    const store = setupStoreWithPresets();
    mountHarness();
    press("3");
    expect(store.activePresetId).toBe("chat-focus");
  });

  it("Ctrl+K fires palette:open", () => {
    setupStoreWithPresets();
    mountHarness();
    const handler = vi.fn();
    window.addEventListener(LAYOUT_EVENTS.PALETTE_OPEN, handler);
    press("k", { ctrl: true });
    expect(handler).toHaveBeenCalledOnce();
    window.removeEventListener(LAYOUT_EVENTS.PALETTE_OPEN, handler);
  });

  it("Ctrl+Shift+L fires layout:edit-requested", () => {
    setupStoreWithPresets();
    mountHarness();
    const handler = vi.fn();
    window.addEventListener(LAYOUT_EVENTS.EDIT_REQUESTED, handler);
    press("L", { ctrl: true, shift: true });
    expect(handler).toHaveBeenCalledOnce();
    window.removeEventListener(LAYOUT_EVENTS.EDIT_REQUESTED, handler);
  });

  it("preset switch ignored when an input is focused", () => {
    const store = setupStoreWithPresets();
    mountHarness();
    const input = document.createElement("input");
    document.body.appendChild(input);
    press("2", { ctrl: true, target: input });
    expect(store.activePresetId).toBe("chat-focus"); // unchanged
  });

  it("Ctrl+K still fires even when an input is focused", () => {
    setupStoreWithPresets();
    mountHarness();
    const input = document.createElement("input");
    document.body.appendChild(input);
    const handler = vi.fn();
    window.addEventListener(LAYOUT_EVENTS.PALETTE_OPEN, handler);
    press("k", { ctrl: true, target: input });
    expect(handler).toHaveBeenCalledOnce();
    window.removeEventListener(LAYOUT_EVENTS.PALETTE_OPEN, handler);
  });
});
