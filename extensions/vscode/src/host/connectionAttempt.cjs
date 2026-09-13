function createConnectionAttemptOwner() {
  let generation = 0
  return {
    begin() {
      const ownedGeneration = ++generation
      return { isCurrent: () => ownedGeneration === generation }
    },
    invalidate() {
      generation++
    },
  }
}

module.exports = { createConnectionAttemptOwner }
