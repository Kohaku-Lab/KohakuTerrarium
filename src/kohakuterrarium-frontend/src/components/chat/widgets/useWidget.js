/**
 * Widget context for nested renderer components, and the optional surface
 * opener a layout provides so chat stubs can reveal side-panel widgets.
 */

import { inject, provide } from "vue"

const WIDGET_KEY = Symbol("kt-widget")
export const WIDGET_SURFACE_KEY = Symbol("kt-widget-surface")

export function provideWidget(context) {
  provide(WIDGET_KEY, context)
}

/** ``{widget, host}`` of the nearest enclosing widget renderer. */
export function useWidget() {
  const context = inject(WIDGET_KEY, null)
  if (!context) throw new Error("useWidget() used outside a widget renderer")
  return context
}

/** ``{surfaces, open(surface, widgetId)}`` from the layout, or ``null`` when it has none. */
export function useWidgetSurfaceOpener() {
  return inject(WIDGET_SURFACE_KEY, null)
}
