/**
 * Marketplace HTTP wrappers — thin axios pass-throughs to
 * /api/catalog/marketplace/*.
 *
 * Lives separately from utils/api.js because that file is already
 * 1132 lines + the per-host axios interceptor wiring (active-host
 * router) means we get the right baseURL automatically without any
 * extra ceremony here.  Re-uses the same axios instance via the
 * default export.
 */

import api from "@/utils/api"

const PREFIX = "/catalog/marketplace"

export const marketplaceAPI = {
  /** List every package across configured sources. */
  async listPackages() {
    const { data } = await api.get(`${PREFIX}/packages`)
    return data
  },

  /** Detail view for one package (newest non-yanked version resolved). */
  async getPackage(name) {
    const { data } = await api.get(`${PREFIX}/packages/${encodeURIComponent(name)}`)
    return data
  },

  /** Filter + search across all sources. */
  async search({ q = "", tag = null, author = null } = {}) {
    const params = {}
    if (q) params.q = q
    if (tag) params.tag = tag
    if (author) params.author = author
    const { data } = await api.get(`${PREFIX}/search`, { params })
    return data
  },

  /** Force cache bust + re-fetch.  Backend returns {ok, packages}. */
  async refresh() {
    const { data } = await api.post(`${PREFIX}/refresh`)
    return data
  },

  /** Configured source list. */
  async listSources() {
    const { data } = await api.get(`${PREFIX}/sources`)
    return data
  },

  /** Add a source — body: {url, alias?}.  Admin-gated on multi-user hosts. */
  async addSource({ url, alias = null }) {
    const { data } = await api.post(`${PREFIX}/sources`, { url, alias })
    return data
  },

  /** Remove a source by URL or alias.  Admin-gated on multi-user hosts. */
  async removeSource(target) {
    // Backend exposes ``?target=...`` rather than a path param
    // because URL sources contain slashes that don't serialize
    // cleanly into a path even with FastAPI's ``{x:path}`` catch-
    // all.  Aliases work in either shape; URLs only via query.
    const { data } = await api.delete(`${PREFIX}/sources`, { params: { target } })
    return data
  },

  /**
   * Install by spec (@name / @name@version / @source/name / git URL / local path).
   *
   * Routes through ``install_package_spec`` on the backend, which
   * dispatches by spec shape: ``@``-form goes through the marketplace
   * resolver + ref-pinned clone; everything else falls through to
   * ``install_package``.  ``editable`` is honored only for local
   * paths (backend rejects ``editable`` on marketplace specs).
   *
   * Blocks until the install completes; the caller is expected to
   * show its own progress indicator.
   */
  async install({ spec, name = null, editable = false }) {
    const { data } = await api.post(`${PREFIX}/install`, { spec, name, editable })
    return data
  },
}

export default marketplaceAPI
