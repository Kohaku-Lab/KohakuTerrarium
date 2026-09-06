const noop = () => {}

export const useClusterStore = () => ({ isCluster: false, markSiteOffline: noop })
export const useInstancesStore = () => ({ current: null })
export const useLocaleStore = () => ({ current: 'en' })
export const useMessagesStore = () => ({ addChannelMessage: noop })
export const useNotificationsStore = () => ({ push: noop })
export const useStatusStore = () => ({ reset: noop, handleActivity: noop })
