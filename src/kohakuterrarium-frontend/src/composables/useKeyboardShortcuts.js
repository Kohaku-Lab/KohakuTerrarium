/**
 * Global keyboard shortcut composable. Called once at app root.
 *
 * Shortcuts:
 *   Ctrl+1..6         switch to the corresponding preset
 *   Ctrl+Shift+L      fire layout:edit-requested
 *   Ctrl+K            fire palette:open (Phase 10 handles it)
 *
 * Shortcuts are ignored when an editable element is focused, EXCEPT
 * Ctrl+K which always wins (matching Slack / Linear / VS Code palette
 * conventions).
 */

import { onMounted, onUnmounted } from "vue";

import { useLayoutStore } from "@/stores/layout";
import {
  fireLayoutEditRequested,
  firePaletteOpen,
} from "@/utils/layoutEvents";

const PRESET_ORDER = [
  "chat-focus",
  "workspace",
  "multi-creature",
  "canvas",
  "debug",
  "settings",
];

function _isEditable(el) {
  if (!el) return false;
  if (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT") {
    return true;
  }
  if (el.isContentEditable) return true;
  return false;
}

export function useKeyboardShortcuts() {
  const layout = useLayoutStore();

  function onKeyDown(e) {
    const ctrl = e.ctrlKey || e.metaKey;
    if (!ctrl) return;

    const editable = _isEditable(e.target);

    // Ctrl+K always wins — opens the command palette.
    if (e.key === "k" || e.key === "K") {
      e.preventDefault();
      firePaletteOpen();
      return;
    }

    // Ctrl+Shift+L toggles edit mode.
    if (e.shiftKey && (e.key === "l" || e.key === "L")) {
      e.preventDefault();
      fireLayoutEditRequested();
      return;
    }

    // Ctrl+1..6 — preset switch. Block in editable fields.
    if (!e.shiftKey && !editable) {
      const idx = Number(e.key) - 1;
      if (idx >= 0 && idx < PRESET_ORDER.length) {
        const id = PRESET_ORDER[idx];
        if (layout.allPresets[id]) {
          e.preventDefault();
          layout.switchPreset(id);
        }
      }
    }
  }

  onMounted(() => {
    if (typeof window === "undefined") return;
    window.addEventListener("keydown", onKeyDown);
  });

  onUnmounted(() => {
    if (typeof window === "undefined") return;
    window.removeEventListener("keydown", onKeyDown);
  });
}
