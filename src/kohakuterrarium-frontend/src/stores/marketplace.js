import { defineStore } from "pinia"

import { marketplaceAPI } from "@/utils/marketplaceApi"

/**
 * Marketplace browse + source-list state.
 *
 * Single source of truth for the in-app "Browse" view of the
 * TerrariumMarket-backed registry.  Mirrors the standalone
 * TerrariumMarket site's stores/registry.js shape so the two
 * code paths feel the same (the API is /api/catalog/marketplace
 * instead of raw GitHub).
 *
 * The store does NOT cache aggressively — the backend caches via
 * packages/marketplace.py's disk cache (1h TTL, ETag-conditional),
 * so re-fetching here is cheap.  An ``invalidate`` action calls
 * the backend's ``/refresh`` endpoint when the user clicks
 * Refresh.
 */
export const useMarketplaceStore = defineStore("marketplace", {
  state: () => ({
    /** @type {Array<object>} packages from /packages */
    packages: [],
    /** @type {Array<{alias:string,url:string,added?:string}>} */
    sources: [],
    loading: false,
    error: null,
    lastFetched: 0,
  }),

  getters: {
    byName: (state) => (name) => state.packages.find((p) => p.name === name) || null,
    allTags: (state) => {
      const set = new Set()
      for (const p of state.packages) for (const t of p.tags || []) set.add(t)
      return [...set].sort()
    },
  },

  actions: {
    async fetch({ force = false } = {}) {
      if (this.loading) return
      if (!force && this.packages.length > 0) return
      this.loading = true
      this.error = null
      try {
        const data = await marketplaceAPI.listPackages()
        this.packages = data.packages || []
        this.sources = data.sources || []
        this.lastFetched = Date.now()
      } catch (err) {
        this.error = err
        console.error("Failed to load marketplace:", err)
      } finally {
        this.loading = false
      }
    },

    /**
     * Backend-side cache bust + re-fetch.  Distinct from passing
     * ``force: true`` to ``fetch`` (which only bypasses the in-store
     * "have we ever loaded" check) — this also forces the Python
     * marketplace module to round-trip to upstream registry.yaml.
     */
    async invalidate() {
      this.loading = true
      this.error = null
      try {
        await marketplaceAPI.refresh()
        const data = await marketplaceAPI.listPackages()
        this.packages = data.packages || []
        this.sources = data.sources || []
        this.lastFetched = Date.now()
      } catch (err) {
        this.error = err
      } finally {
        this.loading = false
      }
    },

    search({ query = "", tag = null }) {
      const q = (query || "").trim().toLowerCase()
      const tagN = (tag || "").trim().toLowerCase()
      return this.packages.filter((p) => {
        if (q && !p.name.toLowerCase().includes(q) && !p.description.toLowerCase().includes(q))
          return false
        if (tagN && !(p.tags || []).map((t) => t.toLowerCase()).includes(tagN)) return false
        return true
      })
    },

    /**
     * Install ``@<name>`` via the backend route.  Blocks until done
     * — callers showing a progress UI should switch to the WebSocket
     * surface once that wiring lands.  Returns the resolved package
     * name on success; throws otherwise (caller handles toast).
     */
    async install(spec) {
      const data = await marketplaceAPI.install({ spec })
      return data.name
    },

    async addSource({ url, alias = null }) {
      const data = await marketplaceAPI.addSource({ url, alias })
      this.sources = data.sources || this.sources
      // Source change may add new packages — refresh.
      await this.invalidate()
      return data.added
    },

    async removeSource(target) {
      const data = await marketplaceAPI.removeSource(target)
      this.sources = data.sources || this.sources
      await this.invalidate()
    },
  },
})
