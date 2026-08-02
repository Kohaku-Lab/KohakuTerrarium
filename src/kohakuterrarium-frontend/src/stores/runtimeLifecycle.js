import { useConversationsStore } from "@/stores/conversations"
import { useInstancesStore } from "@/stores/instances"
import { getRuntimeScope } from "@/stores/runtimeScope"
import { sessionAPI } from "@/utils/api"

export async function stopRuntime(runtimeId) {
  const scope = getRuntimeScope()
  await sessionAPI.stopActive(runtimeId)
  if (scope !== getRuntimeScope()) {
    throw new Error("Runtime host changed while stopping")
  }

  const conversations = useConversationsStore()
  if (!conversations.markRuntimeStopped(runtimeId, scope)) {
    throw new Error("Runtime host changed while stopping")
  }
  useInstancesStore().markRuntimeStopped(runtimeId)
  void conversations.fetchAll({ force: true })
}
