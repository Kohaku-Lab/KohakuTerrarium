//! A session shown in a window of its own, next to the main window.

use serde_json::Value;
use tauri::webview::PageLoadEvent;
use tauri::{AppHandle, Manager, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use url::Url;

use crate::backend::Backend;
use crate::commands::Args;
use crate::navigation::is_internal;
use crate::overlay::is_label;
use crate::smoke;
use crate::window::THEME_SYNC;

pub const LABEL_PREFIX: &str = "popout-";
/// Query parameter that makes the app page show one session instead of the whole app.
pub const POPOUT_PARAM: &str = "kt_popout";

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

/// Open the window described by `spec` (`label`, `scope`, optional `title`), or show it if open.
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
    WebviewWindowBuilder::new(app, label, WebviewUrl::External(url))
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
        .visible(smoke::output_path().is_none())
        .build()
        .is_ok()
}

/// Opens a session window; called by the main window.
#[tauri::command]
pub async fn open_popout_window(app: AppHandle, args: Args) -> bool {
    args.as_ref()
        .and_then(|values| values.first())
        .is_some_and(|spec| open(&app, spec))
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
}
