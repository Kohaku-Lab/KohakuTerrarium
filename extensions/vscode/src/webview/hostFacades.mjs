import { installHistoryBridge } from './historyBridge.mjs'
import { installModelBridge } from './modelBridge.mjs'
import { installSubagentBridge } from './subagentBridge.mjs'

// Installs the fixed Host request facades the shared leaves call. The model bridge
// also receives the reactive ready owner so a stale-ready call sends nothing.
export function installHostFacades({ request, getOwner }) {
  return [installHistoryBridge({ request }), installSubagentBridge({ request }), installModelBridge({ request, getOwner })]
}
