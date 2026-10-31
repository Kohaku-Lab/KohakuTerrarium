import { onBeforeUnmount, onMounted, watch } from "vue"

import { TRAY_ITEM_EVENT, runTrayItem, trayItemList } from "@/ext/trayItems"
import { setTrayItems } from "@/utils/hostBridge"

/** Keep the tray menu's extension items in step with the registered `tray-item` contributions and run the one chosen. */
export function useDesktopTrayItems() {
  let sent = "[]"
  let stop = () => {}

  function onEvent(event) {
    void runTrayItem(event.detail?.id)
  }

  onMounted(() => {
    window.addEventListener(TRAY_ITEM_EVENT, onEvent)
    stop = watch(
      () => JSON.stringify(trayItemList()),
      (json) => {
        if (json === sent) return
        sent = json
        void setTrayItems(JSON.parse(json))
      },
      { immediate: true },
    )
  })
  onBeforeUnmount(() => {
    window.removeEventListener(TRAY_ITEM_EVENT, onEvent)
    stop()
  })
}
