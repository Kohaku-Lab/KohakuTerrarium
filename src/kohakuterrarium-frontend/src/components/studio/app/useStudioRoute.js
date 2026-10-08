/**
 * Where Studio is: the overview, a creature, a module, or making something
 * new. One module-level cell shared by the rail navigator and the Studio
 * surface, remembered per browser together with the workspace it belongs to.
 */

import { ref } from "vue"

const ROUTE_KEY = "kt.studio.route"
const WORKSPACE_KEY = "kt.studio.workspace"
export const PROJECT_REF = "@"
const VIEWS = ["overview", "creature", "module", "new"]
const OVERVIEW = Object.freeze({ view: "overview" })

function readJson(key, fallback) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : fallback
  } catch {
    return fallback
  }
}

function writeJson(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* the place just isn't remembered */
  }
}

function normalize(route) {
  if (!route || !VIEWS.includes(route.view)) return { ...OVERVIEW }
  if (route.view === "creature" && !route.name) return { ...OVERVIEW }
  if (route.view === "module" && !(route.kind && route.name)) return { ...OVERVIEW }
  return { ...route }
}

const route = ref(normalize(readJson(ROUTE_KEY, OVERVIEW)))
const workspaceRef = ref(readJson(WORKSPACE_KEY, PROJECT_REF) || PROJECT_REF)

export function goStudio(next) {
  route.value = normalize(next)
  writeJson(ROUTE_KEY, route.value)
}

/** Remember which workspace Studio reopens (a path, or "@" for the local project). */
export function rememberWorkspace(ref_) {
  workspaceRef.value = ref_ || PROJECT_REF
  writeJson(WORKSPACE_KEY, workspaceRef.value)
}

export function useStudioRoute() {
  return { route, workspaceRef, goStudio, rememberWorkspace }
}

export function _resetStudioRouteForTests() {
  route.value = normalize(readJson(ROUTE_KEY, OVERVIEW))
  workspaceRef.value = readJson(WORKSPACE_KEY, PROJECT_REF) || PROJECT_REF
}
