import { computed, onScopeDispose, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { sessionAPI, terrariumAPI } from '@/utils/api'
import { useI18n } from '@/utils/i18n'
import { createModelInventory, provideModelSwitcherContext } from '@kohakuterrarium/chat-ui'

import { createMetadataReadback, selectOwnedMetadata } from './modelMetadata.mjs'

// VS Code binding of the one shared model picker: adapts the webview's own
// topology/selection state and the Host model facade to the narrow context seam.
const hostBridge = { switchCreatureModel: terrariumAPI.switchCreatureModel, getActive: sessionAPI.getActive }

export function installExtensionModelSwitcher({ chat, getSession, selectTarget, getHostEpoch, bridge = hostBridge }) {
  const { t } = useI18n()
  // The directory is host-scoped: a new backend/ready ownership (new connection)
  // invalidates the cache instead of leaking the previous host's models.
  const inventory = createModelInventory({
    retainPreviousHosts: false,
    getHostKey: () => {
      const runtime = getSession()?.session?.runtimeId || ''
      return `${runtime}#${getHostEpoch()}`
    },
  })

  const instance = computed(() => {
    const current = getSession()
    if (!current?.session) return null
    const session = current.session
    return {
      id: session.runtimeId,
      graph_id: session.runtimeId,
      type: session.kind,
      has_root: (session.creatures || []).some((creature) => creature.name === 'root'),
      creatures: (session.creatures || []).map((creature) => ({
        id: creature.id,
        name: creature.name,
        is_root: creature.name === 'root',
      })),
      llm_name: '',
      model: '',
    }
  })
  const isTerrarium = computed(() => instance.value?.type === 'terrarium')
  const targetOptions = computed(() =>
    isTerrarium.value ? (getSession()?.session?.creatures || []).map((creature) => ({ value: creature.name, label: creature.name })) : [],
  )
  const selectedTarget = computed(() => getSession()?.target || null)
  const currentModel = computed(() => {
    const target = selectedTarget.value
    if (!target) return ''
    const live = chat.modelByTab[target]
    return live?.llmName || live?.model || ''
  })

  // The webview tearing down invalidates every outstanding read-back, so a late
  // response never mutates chat state or warns on an unmounted picker.
  let disposed = false
  onScopeDispose(() => {
    disposed = true
  })

  const readback = createMetadataReadback({
    getOwner: () => ({
      runtimeId: getSession()?.session?.runtimeId,
      creatureId: getSession()?.targetCreatureId,
      epoch: getHostEpoch(),
    }),
    getEntry: (entryKey) => chat.modelByTab[entryKey],
  })

  // A read-back of the active identity applies per-creature model/max_context to
  // ONLY the creature it owned; it never claims a rollback and is never retried.
  function applyMetadata(metadata, target, canonical) {
    const entry = selectOwnedMetadata(metadata, target, canonical, chat.modelByTab[target])
    if (entry) chat.modelByTab[target] = entry
  }

  // Owner-fenced read-back: fenced before dispatch and before apply, so a stale
  // response can never overwrite a newer target/session, a superseded switch, or
  // a newer per-creature session_info.
  async function refreshMetadata(captured, target, canonical) {
    if (disposed || !readback.owns(captured)) return
    let metadata
    try {
      metadata = await bridge.getActive(captured.owner.runtimeId)
    } catch (cause) {
      // Report the unconfirmed read-back only while this exact switch still owns
      // the target; a superseded or torn-down read-back is not the user's problem.
      if (disposed || !readback.owns(captured)) return
      ElMessage.warning(t('modelSwitcher.metadataUnconfirmed', { message: cause?.message || cause }))
      return
    }
    if (disposed || !readback.owns(captured)) return
    applyMetadata(metadata, target, canonical)
  }

  async function switchModel({ target, selector }) {
    const current = getSession()
    const session = current?.session?.runtimeId
    const creature = target || current?.target
    if (!session || !creature) throw Error('Select a creature first')
    // Capture the stable owner so a selected-creature change between the Host
    // post and the webview delivery at the same ready cannot write new context.
    const owner = readback.captureOwner()
    const data = await bridge.switchCreatureModel(session, creature, selector)
    const canonical = data?.model || selector
    if (disposed || !readback.ownsOwner(owner)) throw Error('Selected Creature changed')
    // The backend already accepted the canonical selector; keep it even if the
    // follow-up metadata read-back fails (the creature's session_info confirms).
    chat.modelByTab[creature] = { ...(chat.modelByTab[creature] || {}), model: canonical, llmName: canonical }
    refreshMetadata(readback.captureEntry(creature, owner), creature, canonical)
    return canonical
  }

  provideModelSwitcherContext({
    instance,
    isTerrarium,
    targetOptions,
    selectedTarget,
    currentModel,
    selectTarget,
    switchModel,
    inventory,
    onHostChange: (callback) =>
      watch(
        () => getHostEpoch(),
        () => callback(),
      ),
    onOpenRequest: () => () => {},
  })

  return inventory
}
