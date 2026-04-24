/**
 * Studio REST client — talks to /api/studio/* only.
 *
 * Separate from utils/api.js by design: studio's backend is a
 * sibling router package, studio's frontend is an isolated
 * `/studio/**` tree. Shared axios transport is fine, but the
 * endpoints + shapes live here so runner code can't accidentally
 * depend on studio's surface.
 */

import axios from "axios"

const http = axios.create({
  baseURL: "/api/studio",
  timeout: 30000,
})

/** Unwrap FastAPI's { detail: {...} } error envelope into a plain Error. */
function unwrapError(err) {
  const detail = err?.response?.data?.detail
  if (detail && typeof detail === "object") {
    const e = new Error(detail.message || err.message)
    e.code = detail.code
    e.status = err.response.status
    e.raw = detail
    return e
  }
  return err
}

async function run(promise) {
  try {
    const { data } = await promise
    return data
  } catch (err) {
    throw unwrapError(err)
  }
}

export const metaAPI = {
  health: () => run(http.get("/meta/health")),
  version: () => run(http.get("/meta/version")),
}

export const workspaceAPI = {
  get: () => run(http.get("/workspace")),
  open: (path) => run(http.post("/workspace/open", { path })),
  close: () => run(http.post("/workspace/close")),
}

export const manifestAPI = {
  /** Append a workspace-authored module into kohaku.yaml. Idempotent;
   *  returns {added: false} when the entry already exists. */
  sync: (kind, name) => run(http.post("/workspace/manifest/sync", { kind, name })),
}

export const creatureAPI = {
  list: () => run(http.get("/creatures")),
  load: (name) => run(http.get(`/creatures/${encodeURIComponent(name)}`)),
  scaffold: (body) => run(http.post("/creatures", body)),
  save: (name, body) => run(http.put(`/creatures/${encodeURIComponent(name)}`, body)),
  del: (name) => run(http.delete(`/creatures/${encodeURIComponent(name)}?confirm=true`)),
  readPrompt: (name, rel) => run(http.get(`/creatures/${encodeURIComponent(name)}/prompts/${rel}`)),
  writePrompt: (name, rel, content) =>
    run(http.put(`/creatures/${encodeURIComponent(name)}/prompts/${rel}`, { content })),
}

export const moduleAPI = {
  list: (kind) => run(http.get(`/modules/${kind}`)),
  load: (kind, name) => run(http.get(`/modules/${kind}/${encodeURIComponent(name)}`)),
  scaffold: (kind, body) => run(http.post(`/modules/${kind}`, body)),
  save: (kind, name, body) => run(http.put(`/modules/${kind}/${encodeURIComponent(name)}`, body)),
  loadDoc: (kind, name) => run(http.get(`/modules/${kind}/${encodeURIComponent(name)}/doc`)),
  saveDoc: (kind, name, content) =>
    run(http.put(`/modules/${kind}/${encodeURIComponent(name)}/doc`, { content })),
  del: (kind, name) =>
    run(http.delete(`/modules/${kind}/${encodeURIComponent(name)}?confirm=true`)),
}

export const catalogAPI = {
  tools: () => run(http.get("/catalog/tools")),
  toolDoc: (name) => run(http.get(`/catalog/tools/${encodeURIComponent(name)}/doc`)),
  subagents: () => run(http.get("/catalog/subagents")),
  subagentDoc: (name) => run(http.get(`/catalog/subagents/${encodeURIComponent(name)}/doc`)),
  triggers: () => run(http.get("/catalog/triggers")),
  plugins: () => run(http.get("/catalog/plugins")),
  inputs: () => run(http.get("/catalog/inputs")),
  outputs: () => run(http.get("/catalog/outputs")),
  models: () => run(http.get("/catalog/models")),
  pluginHooks: () => run(http.get("/catalog/plugin_hooks")),
  embeddingPresets: () => run(http.get("/catalog/embedding_presets")),
}

export const packagesAPI = {
  list: () => run(http.get("/packages")),
  creatures: (name) => run(http.get(`/packages/${encodeURIComponent(name)}/creatures`)),
  modules: (name, kind) => run(http.get(`/packages/${encodeURIComponent(name)}/modules/${kind}`)),
}

export const templateAPI = {
  list: () => run(http.get("/templates")),
  render: (id, context) => run(http.post("/templates/render", { id, context })),
}

export const validateAPI = {
  creature: (config) => run(http.post("/validate/creature", { config })),
  module: (kind, source) => run(http.post("/validate/module", { kind, source })),
}

export const schemaAPI = {
  /** Fetch the option schema for a module entry.
   *
   * @param {{kind, name, type, module?, class_name?}} entry
   * @returns {Promise<{params: object[], warnings: object[]}>}
   */
  moduleSchema: (entry) => run(http.post("/module_schema", entry)),
}

export default {
  meta: metaAPI,
  workspace: workspaceAPI,
  manifest: manifestAPI,
  creatures: creatureAPI,
  modules: moduleAPI,
  catalog: catalogAPI,
  packages: packagesAPI,
  templates: templateAPI,
  validate: validateAPI,
  schema: schemaAPI,
}
