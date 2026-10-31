import api from "@/utils/api"

export const appOpenAPI = {
  /** Make a session file on this machine available as a saved session; resolves its name. */
  async openSession(path) {
    const { data } = await api.post("/app/open-session", { path })
    return data.session_name
  },
}
