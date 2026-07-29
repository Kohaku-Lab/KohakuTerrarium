import { useAuthStore } from "@/stores/auth"
import { useHostsStore } from "@/stores/hosts"

const SAME_ORIGIN_SCOPE = "_same_origin"

export function getRuntimeScope() {
  const hosts = useHostsStore()
  const user = hosts.activeUser
  const auth = useAuthStore()
  const sameOriginUser = auth.sameOriginUser
  const userScope =
    user?.id ||
    user?.username ||
    hosts.activeUserToken ||
    sameOriginUser?.id ||
    sameOriginUser?.username ||
    "__anonymous__"
  return `${hosts.activeHostId || SAME_ORIGIN_SCOPE}:${userScope}`
}
