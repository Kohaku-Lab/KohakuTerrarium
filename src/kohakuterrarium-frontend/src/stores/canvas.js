/**
 * Canvas store — artifact list derived from the chat
 * stream. Frontend-only: no backend endpoint.
 *
 * Phase 7 scope: detect long code / markdown / html chunks in
 * assistant messages or explicit `##canvas##` / `##artifact##`
 * markers, index them by source message id, and expose them to the
 * Canvas panel. Regeneration of the same source block appends a
 * version rather than a new artifact.
 */

import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { useChatStore } from "@/stores/chat";

const MIN_LINES_FOR_HEURISTIC = 15;
// Match fenced code blocks: opening ```lang\n ... closing ```
// Uses \n``` on its own line (not $ anchor which is fragile with \r\n).
const CODE_FENCE = /```(\w*)\n([\s\S]*?)\n```/g;
// Simple multi-line extractor for fenced blocks or explicit markers.

/**
 * Best-effort language guess from the opening fence info string, or
 * `text` when no hint is present.
 */
function _langOrText(info) {
  if (!info) return "text";
  const s = String(info).trim().toLowerCase();
  return s || "text";
}

function _guessTypeFromLang(lang) {
  if (!lang) return "code";
  if (lang === "md" || lang === "markdown") return "markdown";
  if (lang === "html" || lang === "htm") return "html";
  if (lang === "svg") return "svg";
  if (lang === "mermaid") return "diagram";
  return "code";
}

function _artifactName(seed) {
  const trimmed = (seed || "").trim().split("\n")[0] || "artifact";
  return trimmed.length > 60 ? trimmed.slice(0, 60) + "…" : trimmed;
}

export const useCanvasStore = defineStore("canvas", () => {
  /** @type {import('vue').Ref<Array<{id: string, name: string, type: string, content: string, lang: string, sourceId: string}>>} */
  const artifacts = ref([]);
  const activeId = ref(/** @type {string | null} */ (null));
  /** Per-session dismissal: once the user closes canvas, don't auto-open. */
  const dismissed = ref(false);

  /** Upsert an artifact. Skips if sourceId exists with same content. */
  function upsertArtifact({ sourceId, content, lang, type, seedName }) {
    const existing = artifacts.value.find((a) => a.sourceId === sourceId);
    if (existing) {
      if (existing.content === content) return existing;
      existing.content = content;
      existing.lang = lang || existing.lang;
      if (!activeId.value) activeId.value = existing.id;
      return existing;
    }
    const id = `artifact_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
    const a = {
      id,
      sourceId,
      name: _artifactName(seedName || content),
      type: type || _guessTypeFromLang(lang),
      content,
      lang: lang || "text",
    };
    artifacts.value = [...artifacts.value, a];
    if (!activeId.value) activeId.value = id;
    return a;
  }

  /** Scan a single assistant message for fenced blocks or markers.
   *  Assistant messages use `parts: [{type, content}]`, not `.content`. */
  function scanMessage(msg) {
    if (!msg || msg.role !== "assistant") return;
    // Assemble full text from parts (the chat store's message format).
    let text = "";
    if (msg.parts && Array.isArray(msg.parts)) {
      for (const p of msg.parts) {
        if (p.type === "text" && p.content) text += p.content;
      }
    } else if (msg.content) {
      text = String(msg.content);
    }
    if (!text) return;

    // Explicit `##canvas##` / `##artifact##` markers take precedence.
    // Syntax: `##canvas name=foo lang=py##...##canvas##`
    const markerRe =
      /##(?:canvas|artifact)(?:\s+([^#]*))?##\n?([\s\S]*?)##(?:canvas|artifact)##/g;
    let m;
    while ((m = markerRe.exec(text)) !== null) {
      const meta = (m[1] || "").trim();
      const body = m[2] || "";
      const lang = /lang=([\w-]+)/.exec(meta)?.[1] || "text";
      const name = /name=([^\s]+)/.exec(meta)?.[1] || null;
      upsertArtifact({
        sourceId: `${msg.id}:marker:${m.index}`,
        content: body,
        lang,
        type: _guessTypeFromLang(lang),
        seedName: name,
      });
    }

    // Fallback: long fenced code blocks become artifacts.
    CODE_FENCE.lastIndex = 0;
    let f;
    while ((f = CODE_FENCE.exec(text)) !== null) {
      const lang = _langOrText(f[1]);
      const body = f[2] || "";
      const lines = body.split("\n").length;
      if (lines < MIN_LINES_FOR_HEURISTIC) continue;
      upsertArtifact({
        sourceId: `${msg.id}:fence:${f.index}`,
        content: body,
        lang,
        type: _guessTypeFromLang(lang),
      });
    }
  }

  /** Drain the chat store's current tab messages and update artifacts. */
  function syncFromChatStore() {
    const chat = useChatStore();
    const tab = chat.activeTab;
    if (!tab) return;
    const msgs = chat.messagesByTab?.[tab] || [];
    for (const m of msgs) {
      if (m.role !== "assistant") continue;
      scanMessage(m);
    }
  }

  function setActive(id) {
    if (artifacts.value.some((a) => a.id === id)) {
      activeId.value = id;
    }
  }

  function dismiss() {
    dismissed.value = true;
  }

  function reset() {
    artifacts.value = [];
    activeId.value = null;
    dismissed.value = false;
  }

  const activeArtifact = computed(
    () => artifacts.value.find((a) => a.id === activeId.value) || null,
  );

  // activeVersion kept as alias for backward compat — just returns the artifact itself.
  const activeVersion = computed(() => activeArtifact.value);

  return {
    artifacts,
    activeId,
    activeArtifact,
    activeVersion,
    dismissed,
    upsertArtifact,
    scanMessage,
    syncFromChatStore,
    setActive,
    dismiss,
    reset,
  };
});
