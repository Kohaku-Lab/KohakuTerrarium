export function webSearchPresentation(values, t) {
  const text = (key) => t(`settings.search.${key}`)
  const backend = values.backend || "duckduckgo"
  return {
    spacious: true,
    advancedLabel: text("advanced"),
    availabilityLabel: text("availability"),
    unavailable: text("unavailable"),
    autoSave: text("autoSave"),
    saving: text("saving"),
    saved: text("saved"),
    fields: {
      backend: {
        label: text("backend"),
        wide: true,
        availability: true,
        hint: ["duckduckgo", "codex", "deepseek", "deepseek_api"].includes(backend)
          ? text(`hint.${backend}`)
          : "",
        labels: {
          duckduckgo: "DuckDuckGo",
          codex: "Codex",
          deepseek_api: text("direct"),
          deepseek: text("legacy"),
        },
      },
      codex_model: {
        label: text("model"),
        visible: backend === "codex",
        wide: true,
        hint: text("modelHint"),
      },
      deepseek_model: {
        label: text("model"),
        visible: backend === "deepseek",
        wide: true,
        hint: text("legacyHint"),
      },
      need_content: {
        label: text("needContent"),
        visible: backend === "deepseek_api",
        wide: true,
        hint: text("contentHint"),
      },
      localization: {
        label: text("localization"),
        visible: backend === "deepseek_api",
        advanced: true,
        wide: true,
        hint: text("localizationHint"),
        placeholder: text("localizationPlaceholder"),
      },
      fallback: {
        label: text("fallback"),
        advanced: true,
        wide: true,
        hint: text("fallbackHint"),
        labels: { none: text("noFallback"), duckduckgo: "DuckDuckGo" },
      },
    },
  }
}
