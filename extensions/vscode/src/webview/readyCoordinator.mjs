export function createReadyCoordinator({ requestReady, applyReady, applyFailure = async () => {} }) {
  let generation = 0

  return {
    invalidate() {
      generation++
    },
    async reconcile() {
      const ownedGeneration = ++generation
      const isCurrent = () => ownedGeneration === generation
      try {
        const result = await requestReady()
        if (isCurrent()) await applyReady(result, isCurrent)
      } catch (error) {
        if (isCurrent()) await applyFailure(error, isCurrent)
      }
    },
  }
}
