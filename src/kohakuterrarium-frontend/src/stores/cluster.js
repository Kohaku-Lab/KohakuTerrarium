/**
 * Cluster store — single owner of lab-host cluster state.
 *
 * Modes:
 *   - "standalone": no lab.  ``sites`` is empty, ``isCluster`` false.
 *   - "lab-host"  : at least the host is connected.  ``sites`` lists
 *                   every connected site (host + workers).
 *
 * Hydration:
 *   - ``hydrate()`` reads ``GET /api/nodes`` — a 200 response means
 *     lab-host mode (sites list populated); 404 means standalone.
 *   - This single endpoint carries both mode discovery and the site
 *     inventory, keeping the cluster store decoupled from the studio
 *     API surface.
 *
 * Polling:
 *   - 15 s by default.  Each poll fans out N RPCs on the backend
 *     (one ``list_creatures`` per worker for creature counts), so
 *     faster polling is expensive at cluster size.
 *
 * Terminology:
 *   - Wire format: ``node_id``.  We KEEP that field name on
 *     ``cluster.sites[*].nodeId`` so callers can copy field
 *     references between the network layer and the store.
 *   - User-visible copy says "host" / "worker" / "site".  Never
 *     "node" (that's a graph node — different concept).
 *   - Component code references sites by ``siteId`` locally.
 */

import { defineStore } from "pinia"
import { computed, ref } from "vue"

import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { nodesAPI } from "@/utils/api"

const POLL_INTERVAL_MS = 15_000
const HOST_SITE_ID = "_host"

/**
 * Deterministic color hash for a site id.  Returns one of a fixed
 * palette so the same ``worker-1`` is the same color across the UI.
 * The host always renders as ``neutral`` — never gets a color.
 */
const SITE_PALETTE = ["teal", "amber", "iolite", "rose", "violet", "cyan", "lime"]
export function siteColorFor(nodeId) {
  if (!nodeId || nodeId === HOST_SITE_ID) return "neutral"
  let hash = 0
  for (let i = 0; i < nodeId.length; i++) {
    hash = (hash * 31 + nodeId.charCodeAt(i)) & 0xffffffff
  }
  return SITE_PALETTE[Math.abs(hash) % SITE_PALETTE.length]
}

export const useClusterStore = defineStore("cluster", () => {
  /** @type {import('vue').Ref<"standalone"|"lab-host">} */
  const mode = ref("standalone")
  /** @type {import('vue').Ref<Array<{nodeId: string, isHost: boolean, status: string, creatures: number|null}>>} */
  const sites = ref([])
  const error = ref("")
  const lastHydratedAt = ref(0)
  // Most recent freshly-rotated pairing token, used by the
  // Spawn-Client wizard to pre-fill the join command. Lives in
  // memory only — never persisted, so reloading the page clears it.
  const latestToken = ref("")
  function setLatestToken(tok) {
    latestToken.value = tok || ""
  }

  let interval = null

  const isCluster = computed(() => mode.value === "lab-host")
  const siteCount = computed(() => sites.value.length)
  // Pickers only appear when there's a real choice to make.
  const showPickers = computed(() => isCluster.value && siteCount.value >= 2)
  const siteById = computed(() => Object.fromEntries(sites.value.map((s) => [s.nodeId, s])))
  const hostSite = computed(() => sites.value.find((s) => s.isHost) || null)
  const workerSites = computed(() => sites.value.filter((s) => !s.isHost))

  function colorFor(nodeId) {
    return siteColorFor(nodeId)
  }

  /** Look up a site by id, or null. */
  function getSite(nodeId) {
    return siteById.value[nodeId] || null
  }

  async function hydrate() {
    error.value = ""
    // Mode discovery: ``/api/nodes`` is lab-host-only — backend
    // returns 404 in standalone mode (see api/routes/nodes.py:34).  A
    // successful response with at least one site is the cluster
    // signal.  This keeps the cluster store isolated from the studio
    // API surface.
    let resp
    try {
      resp = await nodesAPI.list()
    } catch (e) {
      const status = e?.response?.status
      if (status === 404) {
        mode.value = "standalone"
        sites.value = []
        lastHydratedAt.value = Date.now()
        return
      }
      // Other failures (5xx / network): leave previous mode + sites
      // in place so a transient hiccup doesn't flap the UI.
      error.value = e?.message || String(e)
      lastHydratedAt.value = Date.now()
      return
    }
    mode.value = "lab-host"
    sites.value = (resp?.nodes || []).map((n) => ({
      nodeId: n.node_id,
      isHost: !!n.is_host,
      status: n.status || "unknown",
      creatures: typeof n.creatures === "number" ? n.creatures : null,
    }))
    lastHydratedAt.value = Date.now()
  }

  function startPolling() {
    if (interval) return
    interval = createVisibilityInterval(hydrate, POLL_INTERVAL_MS)
    interval.start()
  }

  function stopPolling() {
    if (!interval) return
    interval.stop()
    interval = null
  }

  /** Reset to a clean state — used by tests and on workspace switch. */
  function reset() {
    stopPolling()
    mode.value = "standalone"
    sites.value = []
    error.value = ""
    lastHydratedAt.value = 0
  }

  /**
   * Mark a site as offline locally + trigger a re-hydrate.
   *
   * Called by WS handlers that detect a worker disconnect (chat / pty
   * proxy close).  The cluster polling cycle is 15 s so a fresh
   * hydrate updates the UI faster than waiting for the next tick.
   * @param {string} nodeId
   */
  async function markSiteOffline(nodeId) {
    if (!nodeId) return
    const idx = sites.value.findIndex((s) => s.nodeId === nodeId)
    if (idx >= 0) {
      // Replace the entry so the reactive array picks up the change.
      sites.value = [
        ...sites.value.slice(0, idx),
        { ...sites.value[idx], status: "unreachable" },
        ...sites.value.slice(idx + 1),
      ]
    }
    try {
      await hydrate()
    } catch {
      /* hydrate already records error */
    }
  }

  return {
    // state
    mode,
    sites,
    error,
    lastHydratedAt,
    latestToken,
    // getters
    isCluster,
    siteCount,
    showPickers,
    siteById,
    hostSite,
    workerSites,
    // actions
    colorFor,
    getSite,
    hydrate,
    startPolling,
    stopPolling,
    reset,
    markSiteOffline,
    setLatestToken,
  }
})

export { HOST_SITE_ID, POLL_INTERVAL_MS }
