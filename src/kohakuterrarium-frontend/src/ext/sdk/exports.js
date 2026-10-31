/**
 * Every named export of ``@kohakuterrarium/ui-sdk`` v1.
 *
 * The shared-module build reads this list to generate the static
 * re-exports in ``/ext-shared/sdk.js``; the SDK and the testing mock
 * must both export exactly these names.
 */
export const SDK_EXPORT_NAMES = [
  "SDK_VERSION",
  "satisfiesRange",
  "defineExtension",
  "useExtension",
  "useOwner",
  "useWidget",
  "useOverlay",
  "useMessageBlock",
  "OwnerCallError",
  "useHost",
  "useSession",
  "useCreature",
  "useSessionEvents",
  "useLocale",
  "useTheme",
  "THEME_TOKENS",
  "notify",
  "notifyDesktop",
  "openExternal",
  "hostInfo",
  "confirm",
  "openTab",
  "FRAME_PROTOCOL",
  "FRAME_MESSAGES",
  "FRAME_SUPPORTED",
  "FrameIsolationUnsupportedError",
  "createFrameBridge",
]
