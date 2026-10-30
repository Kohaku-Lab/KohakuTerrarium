/** Client for ``/api/app/shims``: the opt-in `kt` terminal command. */

import api from "@/utils/api"

export const appShimsAPI = {
  /** Installed state, target directory and PATH status. */
  async status() {
    const { data } = await api.get("/app/shims")
    return data
  },
  /** Write the launcher scripts, optionally adding their directory to PATH. */
  async install({ addToPath = true } = {}) {
    const { data } = await api.post("/app/shims/install", { add_to_path: addToPath })
    return data
  },
  /** Remove the launcher scripts, optionally removing the PATH entry. */
  async uninstall({ removeFromPath = true } = {}) {
    const { data } = await api.post("/app/shims/uninstall", { remove_from_path: removeFromPath })
    return data
  },
}
