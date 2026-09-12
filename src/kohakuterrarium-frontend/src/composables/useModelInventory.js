import { useHostsStore } from "@/stores/hosts"

import {
  MODEL_INVENTORY_FRESH_MS,
  _resetModelInventoryForTests,
  createModelInventory,
} from "./modelInventory"

// Dashboard binding of the one host-neutral inventory: the cache key is the
// active host, so switching hosts invalidates the directory instead of leaking
// the previous host's models. The Extension binds the same factory to its own
// session/ready ownership key through the public seam.
export { MODEL_INVENTORY_FRESH_MS, _resetModelInventoryForTests }

export function useModelInventory() {
  const hosts = useHostsStore()
  return createModelInventory({ getHostKey: () => hosts.activeHostId || "_same_origin" })
}
