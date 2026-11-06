//! Overlay placement belongs to the shell, in one native coordinate space.

use std::collections::HashSet;
use std::sync::Mutex;
use std::time::Duration;

use serde_json::{json, Value};
use tauri::{Manager, WebviewWindow};

use crate::overlay::overlay_get_bounds;
use crate::overlay_placement::overlay_placement;

#[derive(Default)]
pub struct OverlayGeometry {
    pending: Mutex<HashSet<String>>,
}

fn path(window: &WebviewWindow) -> Option<std::path::PathBuf> {
    window
        .app_handle()
        .path()
        .app_config_dir()
        .ok()
        .map(|dir| dir.join(format!("{}.json", window.label())))
}

pub fn load(window: &WebviewWindow) -> Option<Value> {
    serde_json::from_slice(&std::fs::read(path(window)?).ok()?).ok()
}

fn save(window: &WebviewWindow) {
    let Some(value) = overlay_placement(window.clone(), None) else {
        return;
    };
    let Some(path) = path(window) else { return };
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    // A corrupt or interrupted optional placement is ignored on the next launch.
    let _ = std::fs::write(path, value.to_string());
}

/// Coalesce native events and report the final bounds after the setters settle.
pub fn changed(window: WebviewWindow) {
    let app = window.app_handle().clone();
    if !app
        .state::<OverlayGeometry>()
        .pending
        .lock()
        .unwrap()
        .insert(window.label().into())
    {
        return;
    }
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(200));
        app.state::<OverlayGeometry>()
            .pending
            .lock()
            .unwrap()
            .remove(window.label());
        save(&window);
        if let Some(bounds) = overlay_get_bounds(window.clone()) {
            let payload = json!(bounds);
            let _ = window.eval(format!("window.dispatchEvent(new CustomEvent('kt-overlay-native-bounds',{{detail:{payload}}}))"));
        }
    });
}

pub fn closing(window: &WebviewWindow) {
    save(window);
}
