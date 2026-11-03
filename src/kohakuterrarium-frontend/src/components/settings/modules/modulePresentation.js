import { webSearchPresentation } from "./searchPresentation"
import strings from "@/utils/i18n/locales/settingsModules"

function translated(t, key, fallback) {
  const value = t(key)
  return value === key ? fallback : value
}

export function moduleDescription(module, t) {
  return translated(
    t,
    `settings.moduleInfo.${module.type}.${module.name}`,
    module.description || "",
  )
}

export function moduleSearchText(module) {
  const key = `settings.moduleInfo.${module.type}.${module.name}`
  return [module.name, module.description, ...Object.values(strings).map((locale) => locale[key])]
    .filter(Boolean)
    .join(" ")
}

export function modulePresentation(module, values, t) {
  const prefix = `settings.moduleInfo.${module.type}.${module.name}`
  const known = translated(t, prefix, null) !== null
  const reason = (message) => {
    if (message === "DeepSeek API key is not configured. Run: kt config key set deepseek") {
      return t("settings.moduleForm.deepseekUnavailable", { command: "kt config key set deepseek" })
    }
    if (message === "Codex subscription is not connected. Run: kt login codex") {
      return t("settings.moduleForm.codexUnavailable", { command: "kt login codex" })
    }
    return message
  }
  const fields = Object.fromEntries(
    Object.entries(module.schema || {}).map(([key, rawSpec]) => {
      const spec = rawSpec || {}
      const fieldText = (part, fallback) =>
        known
          ? translated(
              t,
              `${prefix}.${key}.${part}`,
              translated(t, `settings.moduleField.${key}.${part}`, fallback),
            )
          : fallback
      return [
        key,
        {
          label: fieldText("label", spec.label),
          doc: fieldText("doc", spec.doc || spec.description),
          hint: spec.hint,
          placeholder: fieldText("placeholder", spec.placeholder),
          labels: Object.fromEntries(
            (spec.values || []).map((value) => [
              value,
              known
                ? translated(t, `settings.moduleValue.${value}`, spec.labels?.[value] || value)
                : spec.labels?.[value] || value,
            ]),
          ),
          disabledValues: Object.fromEntries(
            Object.entries(spec.disabled_values || {}).map(([value, message]) => [
              value,
              known ? reason(message) : message,
            ]),
          ),
        },
      ]
    }),
  )
  const search =
    module.type === "tool" && module.name === "web_search" ? webSearchPresentation(values, t) : {}
  for (const [key, field] of Object.entries(search.fields || {})) {
    fields[key] = { ...fields[key], ...field, doc: field.hint || fields[key]?.doc }
  }
  return {
    ...search,
    empty: t("settings.moduleForm.empty"),
    listPlaceholder: t("settings.moduleForm.listPlaceholder"),
    invalidJson: t("settings.moduleForm.invalidJson"),
    saveError: t("settings.moduleForm.saveError"),
    autoSave: t("settings.search.autoSave"),
    saving: t("settings.search.saving"),
    saved: t("settings.search.saved"),
    unavailable: t("settings.search.unavailable"),
    fields,
  }
}
