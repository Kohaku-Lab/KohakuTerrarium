//! A system-wide shortcut that raises the app window from anywhere.

use serde_json::{json, Value};
use tauri::{AppHandle, Manager, State, Wry};
use tauri_plugin_global_shortcut::{GlobalShortcut, Shortcut};

use crate::commands::Args;
use crate::settings::ShellSettings;
use crate::window;

/// Page event that asks the app to focus the message box (or the palette).
const SUMMON_SCRIPT: &str = "window.dispatchEvent(new CustomEvent('kt:desktop-summon'))";

/// The accelerator text as the shortcut plugin reads it, e.g. `CommandOrControl+Shift+Space`.
pub fn parse(text: &str) -> Option<Shortcut> {
    text.parse().ok()
}

/// Bring the window forward and tell the page.
pub fn summon(app: &AppHandle) {
    window::focus(app);
    if let Some(main) = app.get_webview_window("main") {
        let _ = main.eval(SUMMON_SCRIPT);
    }
}

fn registrar(app: &AppHandle) -> Option<State<'_, GlobalShortcut<Wry>>> {
    app.try_state::<GlobalShortcut<Wry>>()
}

/// Replace the registered shortcut `previous` with `next`; `false` when `next` cannot be taken.
fn apply(app: &AppHandle, previous: Option<&str>, next: Option<&str>) -> bool {
    let Some(shortcuts) = registrar(app) else {
        return false;
    };
    if let Some(old) = previous.and_then(parse) {
        let _ = shortcuts.unregister(old);
    }
    match next {
        None => true,
        Some(text) => parse(text).is_some_and(|shortcut| shortcuts.register(shortcut).is_ok()),
    }
}

/// Rarely used, and a key all three desktop platforms map (macOS stops at F20).
const PROBE: &str = "Control+Alt+Shift+F19";

/// Register and release the probe shortcut, leaving saved settings alone.
pub fn probe(app: &AppHandle) -> Value {
    let registered = apply(app, None, Some(PROBE));
    let released = registered && apply(app, Some(PROBE), None);
    json!({ "registered": registered, "released": released })
}

/// Register the shortcut saved by a previous run.
pub fn restore(app: &AppHandle) {
    if let Some(saved) = app.state::<ShellSettings>().summon_shortcut() {
        apply(app, None, Some(&saved));
    }
}

#[tauri::command]
pub fn get_summon_shortcut(settings: State<ShellSettings>) -> Value {
    json!({ "shortcut": settings.summon_shortcut() })
}

/// Sets the summon shortcut (a string) or clears it (`null`); `false` leaves the old one in place.
#[tauri::command]
pub fn set_summon_shortcut(app: AppHandle, settings: State<ShellSettings>, args: Args) -> bool {
    let requested = args.as_ref().and_then(|values| values.first());
    let next = match requested {
        Some(Value::String(text)) if parse(text).is_some() => Some(text.clone()),
        Some(Value::Null) | None => None,
        _ => return false,
    };
    let previous = settings.summon_shortcut();
    if !apply(&app, previous.as_deref(), next.as_deref()) {
        apply(&app, None, previous.as_deref());
        return false;
    }
    settings.set_summon_shortcut(next)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accelerators_parse_the_way_the_page_writes_them() {
        for text in [
            "CommandOrControl+Shift+Space",
            "Alt+K",
            "Control+Alt+F5",
            "Super+1",
        ] {
            assert!(parse(text).is_some(), "{text}");
        }
    }

    #[test]
    fn the_probe_shortcut_parses_and_uses_a_key_macos_can_bind() {
        assert!(parse(PROBE).is_some());
        let key: u32 = PROBE.rsplit('F').next().unwrap().parse().unwrap();
        assert!((13..=20).contains(&key));
    }

    #[test]
    fn nonsense_and_modifier_only_text_is_refused() {
        for text in ["", "Space+", "Hyper+Banana", "Ctrl+Shift+", "+"] {
            assert!(parse(text).is_none(), "{text:?}");
        }
    }
}
