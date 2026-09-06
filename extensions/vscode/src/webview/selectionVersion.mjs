export function createSelectionVersionOwner() {
  let activeEpoch = null
  let highestVersion = null
  let unversionedAccepted = false

  function valid(version) {
    return Number.isSafeInteger(version) && version >= 0
  }

  return {
    highest: () => highestVersion,
    acceptBaseline(epoch, version) {
      if (!valid(version)) return false
      if (epoch !== activeEpoch) {
        activeEpoch = epoch
        highestVersion = version
        unversionedAccepted = false
      } else if (highestVersion === null || version > highestVersion) {
        highestVersion = version
      }
      return true
    },
    acceptResult(epoch, version, advanceEpoch = false) {
      if (!valid(version)) throw Error('Host selection result has no valid selectionVersion')
      if (advanceEpoch && epoch !== activeEpoch) {
        activeEpoch = epoch
        highestVersion = null
        unversionedAccepted = false
      }
      if (epoch === activeEpoch && (highestVersion === null || version > highestVersion)) highestVersion = version
      return highestVersion
    },
    beginNotification(epoch, version, advanceEpoch = false) {
      if (activeEpoch === null || (advanceEpoch && epoch !== activeEpoch)) {
        activeEpoch = epoch
        highestVersion = null
        unversionedAccepted = false
      }
      if (epoch !== activeEpoch) return null
      if (!valid(version)) {
        if (highestVersion !== null || unversionedAccepted) return null
        unversionedAccepted = true
        return { isCurrent: () => epoch === activeEpoch && highestVersion === null }
      }
      if (highestVersion !== null && version <= highestVersion) return null
      highestVersion = version
      return { isCurrent: () => epoch === activeEpoch && highestVersion === version }
    },
  }
}
