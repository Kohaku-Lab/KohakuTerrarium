//! A session shown in a window of its own, next to the main window.

use std::sync::Mutex;

use serde_json::{json, Value};
use tauri::webview::PageLoadEvent;
use tauri::{AppHandle, Manager, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use url::Url;

use crate::backend::Backend;
use crate::commands::Args;
use crate::navigation::is_internal;
use crate::overlay::is_label;
use crate::settings::ShellSettings;
use crate::smoke;
use crate::window::{self, THEME_SYNC};

pub const LABEL_PREFIX: &str = "popout-";
/// Query parameter that makes the app page show one session instead of the whole app.
pub const POPOUT_PARAM: &str = "kt_popout";
/// Page event that asks the main window to show the session a window was holding.
pub const RETURN_EVENT: &str = "kt:desktop-popout-return";
const MAX_RESTORED: usize = 8;
const MAX_POSITION: f64 = 20_000.0;

pub fn is_popout_label(label: &str) -> bool {
    is_label(LABEL_PREFIX, label)
}

pub fn popout_url(port: u16, scope: &str) -> Option<Url> {
    Url::parse_with_params(
        &format!("http://127.0.0.1:{port}/"),
        [(POPOUT_PARAM, scope)],
    )
    .ok()
}

/// A session window: its label, the session it shows and its title.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    pub label: String,
    pub scope: String,
    pub title: String,
}

/// The session windows that are open, so they can be reopened at the next launch.
#[derive(Default)]
pub struct PopoutRegistry(Mutex<Vec<Entry>>);

impl PopoutRegistry {
    fn add(&self, entry: Entry) {
        let mut windows = self.0.lock().unwrap();
        windows.retain(|known| known.label != entry.label);
        windows.push(entry);
    }

    fn remove(&self, label: &str) -> Option<Entry> {
        let mut windows = self.0.lock().unwrap();
        let at = windows.iter().position(|known| known.label == label)?;
        Some(windows.remove(at))
    }

    pub fn forget(&self, label: &str) {
        self.remove(label);
    }

    /// The open windows as the settings store keeps them.
    pub fn snapshot(&self) -> Value {
        let windows = self.0.lock().unwrap();
        Value::Array(
            windows
                .iter()
                .map(|e| json!({ "label": e.label, "scope": e.scope, "title": e.title }))
                .collect(),
        )
    }
}

/// The windows a settings value asks to reopen; malformed entries and surplus ones are dropped.
pub fn restorable(value: &Value) -> Vec<Value> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter(|spec| {
            let text = |key: &str| spec.get(key).and_then(Value::as_str).unwrap_or("");
            is_popout_label(text("label")) && !text("scope").is_empty()
        })
        .take(MAX_RESTORED)
        .cloned()
        .collect()
}

/// Where the page asked the window to appear, if it gave a sensible spot.
pub fn position_of(spec: &Value) -> Option<(f64, f64)> {
    let coordinate = |key: &str| {
        spec.get(key)
            .and_then(Value::as_f64)
            .filter(|n| n.is_finite() && n.abs() <= MAX_POSITION)
    };
    Some((coordinate("x")?, coordinate("y")?))
}

/// Open the window described by `spec` (`label`, `scope`, optional `title`, `x`, `y`), or show it if open.
pub fn open(app: &AppHandle, spec: &Value) -> bool {
    let (Some(label), Some(scope)) = (
        spec.get("label").and_then(Value::as_str),
        spec.get("scope").and_then(Value::as_str),
    ) else {
        return false;
    };
    if !is_popout_label(label) || scope.is_empty() {
        return false;
    }
    if let Some(existing) = app.get_webview_window(label) {
        let _ = existing.unminimize();
        return existing.show().is_ok() && existing.set_focus().is_ok();
    }
    let Some(url) = app
        .state::<Backend>()
        .port()
        .and_then(|port| popout_url(port, scope))
    else {
        return false;
    };
    let title = spec
        .get("title")
        .and_then(Value::as_str)
        .unwrap_or("KohakuTerrarium");
    let navigation = app.clone();
    let page_load = app.clone();
    let mut builder = WebviewWindowBuilder::new(app, label, WebviewUrl::External(url))
        .title(title)
        .inner_size(1000.0, 760.0)
        .min_inner_size(520.0, 360.0)
        .initialization_script(THEME_SYNC)
        .on_navigation(move |url| is_internal(url, navigation.state::<Backend>().port()))
        .on_page_load(move |_window, payload| {
            if matches!(payload.event(), PageLoadEvent::Finished) {
                page_load.state::<Backend>().mark_popout_page_loaded();
            }
        })
        .visible(smoke::output_path().is_none());
    if let Some((x, y)) = position_of(spec) {
        builder = builder.position(x, y);
    }
    let built = builder.build().is_ok();
    if built {
        app.state::<PopoutRegistry>().add(Entry {
            label: label.to_string(),
            scope: scope.to_string(),
            title: title.to_string(),
        });
    }
    built
}

/// Reopen the session windows the last run ended with.
pub fn restore(app: &AppHandle) {
    let settings = app.state::<ShellSettings>();
    if !settings.get_bool("restorePopouts") {
        return;
    }
    for spec in restorable(&settings.get("popouts")) {
        open(app, &spec);
    }
}

/// Remember the open session windows for the next launch.
pub fn remember(app: &AppHandle) {
    let snapshot = app.state::<PopoutRegistry>().snapshot();
    let _ = app
        .state::<ShellSettings>()
        .patch(&json!({ "popouts": snapshot }));
}

/// Opens a session window; called by the main window.
#[tauri::command]
pub async fn open_popout_window(app: AppHandle, args: Args) -> bool {
    args.as_ref()
        .and_then(|values| values.first())
        .is_some_and(|spec| open(&app, spec))
}

/// Close the calling session window; with `[true]` the main window first shows that session.
#[tauri::command]
pub fn popout_return(app: AppHandle, window: WebviewWindow, args: Args) -> bool {
    if !is_popout_label(window.label()) {
        return false;
    }
    let show_in_main = args
        .as_ref()
        .and_then(|values| values.first())
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let entry = app.state::<PopoutRegistry>().remove(window.label());
    if let (true, Some(entry), Some(main)) = (show_in_main, entry, app.get_webview_window("main")) {
        window::focus(&app);
        let scope = serde_json::to_string(&entry.scope).unwrap_or_else(|_| "\"\"".into());
        let _ = main.eval(format!(
            "window.dispatchEvent(new CustomEvent('{RETURN_EVENT}', {{ detail: {{ scope: {scope} }} }}))"
        ));
    }
    window.close().is_ok()
}

/// Marks that a session window's page could call a native command (used by the self-check).
#[tauri::command]
pub fn popout_ack(window: WebviewWindow, backend: State<Backend>) -> bool {
    if !is_popout_label(window.label()) {
        return false;
    }
    backend.confirm_popout_ipc();
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(label: &str, scope: &str) -> Entry {
        Entry {
            label: label.into(),
            scope: scope.into(),
            title: scope.to_uppercase(),
        }
    }

    #[test]
    fn only_popout_labels_are_accepted() {
        assert!(is_popout_label("popout-session-1-abcd1234"));
        assert!(!is_popout_label("overlay-a"));
        assert!(!is_popout_label("main"));
        assert!(!is_popout_label("popout-"));
        assert!(!is_popout_label("popout-UPPER"));
    }

    #[test]
    fn the_session_travels_as_one_encoded_parameter() {
        let url = popout_url(8001, "graph a&b=1").unwrap();
        assert_eq!(url.host_str(), Some("127.0.0.1"));
        let pairs: Vec<_> = url.query_pairs().collect();
        assert_eq!(pairs.len(), 1);
        assert_eq!(pairs[0].0, POPOUT_PARAM);
        assert_eq!(pairs[0].1, "graph a&b=1");
    }

    #[test]
    fn the_registry_tracks_open_windows_by_label() {
        let registry = PopoutRegistry::default();
        registry.add(entry("popout-a", "a"));
        registry.add(entry("popout-b", "b"));
        registry.add(entry("popout-a", "a2"));
        assert_eq!(
            registry.snapshot(),
            json!([
                { "label": "popout-b", "scope": "b", "title": "B" },
                { "label": "popout-a", "scope": "a2", "title": "A2" },
            ])
        );
        assert_eq!(registry.remove("popout-b"), Some(entry("popout-b", "b")));
        assert_eq!(registry.remove("popout-b"), None);
        registry.forget("popout-a");
        assert_eq!(registry.snapshot(), json!([]));
    }

    #[test]
    fn only_well_formed_windows_are_reopened_and_not_too_many() {
        let mut windows = vec![
            json!({ "label": "popout-ok", "scope": "s", "title": "T" }),
            json!({ "label": "overlay-x", "scope": "s" }),
            json!({ "label": "popout-noscope", "scope": "" }),
            json!({ "label": "popout-missing" }),
            json!("text"),
        ];
        for n in 0..20 {
            windows.push(json!({ "label": format!("popout-n{n}"), "scope": "s" }));
        }
        let kept = restorable(&Value::Array(windows));
        assert_eq!(kept.len(), MAX_RESTORED);
        assert_eq!(kept[0]["label"], "popout-ok");
        assert!(restorable(&Value::Null).is_empty());
        assert!(restorable(&json!({ "label": "popout-a" })).is_empty());
    }

    #[test]
    fn a_requested_position_must_be_a_pair_of_sensible_numbers() {
        assert_eq!(
            position_of(&json!({ "x": 10, "y": -20.5 })),
            Some((10.0, -20.5))
        );
        assert_eq!(position_of(&json!({ "x": 10 })), None);
        assert_eq!(position_of(&json!({ "x": "10", "y": 5 })), None);
        assert_eq!(position_of(&json!({ "x": 1e9, "y": 5 })), None);
        assert_eq!(position_of(&json!({})), None);
    }
}
