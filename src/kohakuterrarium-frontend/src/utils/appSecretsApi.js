import api from "@/utils/api"

export const appSecretsAPI = {
  /** `{ local, mode, active, available, backend }` for this machine's API-key store. */
  async status() {
    const { data } = await api.get("/app/secret-store")
    return data
  },

  /** Move every stored API key to ``"file"`` or ``"keychain"``; resolves the new status. */
  async setMode(mode) {
    const { data } = await api.post("/app/secret-store", { mode })
    return data
  },
}
