//! Frameless, transparent, always-on-top windows that draw an extension's overlay
//! outside the main window. Only the main window opens them; each moves itself.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use serde_json::Value;
use tauri::{
    AppHandle, LogicalPosition, LogicalSize, Manager, State, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder,
};
use url::Url;

use crate::backend::Backend;
use crate::commands::{number_arg, string_arg, Args};
use crate::navigation::is_internal;
use crate::smoke;

pub const LABEL_PREFIX: &str = "overlay-";
const SCREEN_MARGIN: f64 = 16.0;
const MIN_SIZE: f64 = 48.0;
const MAX_SIZE: f64 = 4096.0;
const HIT_POLL: Duration = Duration::from_millis(30);

/// Query parameter that makes the app page render one overlay instead of the app.
pub const OVERLAY_PARAM: &str = "kt_overlay";

/// Rectangles (window-relative, logical pixels) of each click-through overlay that catch the mouse.
#[derive(Default)]
pub struct HitRegions(Mutex<HashMap<String, Arc<Mutex<Vec<Rect>>>>>);

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Rect {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}

pub fn is_overlay_label(label: &str) -> bool {
    label.strip_prefix(LABEL_PREFIX).is_some_and(|rest| {
        (1..=48).contains(&rest.len())
            && rest
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
    })
}

/// Whether the point lies inside any region.
pub fn hits(regions: &[Rect], x: f64, y: f64) -> bool {
    regions
        .iter()
        .any(|r| x >= r.x && x < r.x + r.w && y >= r.y && y < r.y + r.h)
}

/// Top-left corner of a `size` box anchored inside a `screen` of the given size.
pub fn anchored_position(anchor: &str, screen: (f64, f64), size: (f64, f64)) -> (f64, f64) {
    let right = screen.0 - size.0 - SCREEN_MARGIN;
    let bottom = screen.1 - size.1 - SCREEN_MARGIN;
    match anchor {
        "top-left" => (SCREEN_MARGIN, SCREEN_MARGIN),
        "top-right" => (right, SCREEN_MARGIN),
        "bottom-left" => (SCREEN_MARGIN, bottom),
        "center" => ((screen.0 - size.0) / 2.0, (screen.1 - size.1) / 2.0),
        _ => (right, bottom),
    }
}

struct Spec {
    label: String,
    key: String,
    anchor: String,
    size: (f64, f64),
    position: Option<(f64, f64)>,
    on_top: bool,
}

fn field_number(value: &Value, name: &str) -> Option<f64> {
    value.get(name)?.as_f64()
}

fn parse_spec(value: &Value) -> Option<Spec> {
    let label = value.get("label")?.as_str()?.to_string();
    if !is_overlay_label(&label) {
        return None;
    }
    let size = (
        field_number(value, "width")?.clamp(MIN_SIZE, MAX_SIZE),
        field_number(value, "height")?.clamp(MIN_SIZE, MAX_SIZE),
    );
    let position = match (field_number(value, "x"), field_number(value, "y")) {
        (Some(x), Some(y)) => Some((x, y)),
        _ => None,
    };
    Some(Spec {
        label,
        key: value.get("key")?.as_str()?.to_string(),
        anchor: value
            .get("anchor")
            .and_then(Value::as_str)
            .unwrap_or("bottom-right")
            .to_string(),
        size,
        position,
        on_top: value
            .get("alwaysOnTop")
            .and_then(Value::as_bool)
            .unwrap_or(true),
    })
}

/// Where a new window goes on the primary monitor, in logical pixels.
fn default_position(app: &AppHandle, spec: &Spec) -> (f64, f64) {
    let Ok(Some(monitor)) = app.primary_monitor() else {
        return (SCREEN_MARGIN, SCREEN_MARGIN);
    };
    let scale = monitor.scale_factor();
    let origin = monitor.position();
    let size = monitor.size();
    let screen = (size.width as f64 / scale, size.height as f64 / scale);
    let (x, y) = anchored_position(&spec.anchor, screen, spec.size);
    (origin.x as f64 / scale + x, origin.y as f64 / scale + y)
}

pub fn overlay_url(port: u16, key: &str) -> Option<Url> {
    Url::parse_with_params(&format!("http://127.0.0.1:{port}/"), [(OVERLAY_PARAM, key)]).ok()
}

/// Open the overlay window described by `spec`, or show it if it already exists.
pub fn open(app: &AppHandle, spec: &Value) -> bool {
    let Some(spec) = parse_spec(spec) else {
        return false;
    };
    if let Some(existing) = app.get_webview_window(&spec.label) {
        return existing.show().is_ok();
    }
    let Some(url) = app
        .state::<Backend>()
        .port()
        .and_then(|port| overlay_url(port, &spec.key))
    else {
        return false;
    };
    let (x, y) = spec
        .position
        .unwrap_or_else(|| default_position(app, &spec));
    let navigation = app.clone();
    let page_load = app.clone();
    WebviewWindowBuilder::new(app, &spec.label, WebviewUrl::External(url))
        .title("KohakuTerrarium overlay")
        .decorations(false)
        .transparent(true)
        .shadow(false)
        .resizable(false)
        .skip_taskbar(true)
        .always_on_top(spec.on_top)
        .focused(false)
        .inner_size(spec.size.0, spec.size.1)
        .position(x, y)
        .visible(smoke::output_path().is_none())
        .on_navigation(move |url| is_internal(url, navigation.state::<Backend>().port()))
        .on_page_load(move |_window, payload| {
            if matches!(payload.event(), tauri::webview::PageLoadEvent::Finished) {
                page_load.state::<Backend>().mark_overlay_page_loaded();
            }
        })
        .build()
        .is_ok()
}

/// Opens an overlay window; called by the main window.
#[tauri::command]
pub async fn open_overlay_window(app: AppHandle, args: Args) -> bool {
    args.as_ref()
        .and_then(|values| values.first())
        .is_some_and(|spec| open(&app, spec))
}

/// Closes the overlay window named by the first argument.
#[tauri::command]
pub async fn close_overlay_window(app: AppHandle, args: Args) -> bool {
    let Some(label) = string_arg(&args, 0).filter(|label| is_overlay_label(label)) else {
        return false;
    };
    app.get_webview_window(label)
        .is_some_and(|window| window.close().is_ok())
}

/// Starts the native window drag from the calling overlay window.
#[tauri::command]
pub fn overlay_start_drag(window: WebviewWindow) -> bool {
    is_overlay_label(window.label()) && window.start_dragging().is_ok()
}

/// Moves and resizes the calling overlay: `[x, y, width, height]`, logical pixels.
#[tauri::command]
pub fn overlay_set_bounds(window: WebviewWindow, args: Args) -> bool {
    if !is_overlay_label(window.label()) {
        return false;
    }
    let (Some(x), Some(y), Some(w), Some(h)) = (
        number_arg(&args, 0),
        number_arg(&args, 1),
        number_arg(&args, 2),
        number_arg(&args, 3),
    ) else {
        return false;
    };
    let size = LogicalSize::new(w.clamp(MIN_SIZE, MAX_SIZE), h.clamp(MIN_SIZE, MAX_SIZE));
    window.set_size(size).is_ok() && window.set_position(LogicalPosition::new(x, y)).is_ok()
}

/// Marks that an overlay page could call native commands (used by the headless self-check).
#[tauri::command]
pub fn overlay_ack(window: WebviewWindow, backend: State<Backend>) -> bool {
    if !is_overlay_label(window.label()) {
        return false;
    }
    backend.confirm_overlay_ipc();
    true
}

fn parse_regions(args: &Args) -> Vec<Rect> {
    let Some(list) = args
        .as_ref()
        .and_then(|v| v.first())
        .and_then(Value::as_array)
    else {
        return Vec::new();
    };
    list.iter()
        .filter_map(|item| {
            let parts = item.as_array()?;
            let number = |i: usize| parts.get(i)?.as_f64();
            Some(Rect {
                x: number(0)?,
                y: number(1)?,
                w: number(2)?,
                h: number(3)?,
            })
        })
        .collect()
}

/// Sets the rectangles that catch the mouse; the rest of the calling overlay is click-through.
#[tauri::command]
pub fn overlay_set_hit_regions(
    app: AppHandle,
    window: WebviewWindow,
    args: Args,
    state: State<HitRegions>,
) -> bool {
    let label = window.label().to_string();
    if !is_overlay_label(&label) {
        return false;
    }
    let regions = parse_regions(&args);
    let mut tracked = state.0.lock().unwrap();
    if let Some(existing) = tracked.get(&label) {
        *existing.lock().unwrap() = regions;
        return true;
    }
    let shared = Arc::new(Mutex::new(regions));
    tracked.insert(label.clone(), shared.clone());
    thread::spawn(move || {
        poll_cursor(&window, &shared);
        app.state::<HitRegions>().0.lock().unwrap().remove(&label);
    });
    true
}

/// Toggle cursor pass-through as the pointer enters and leaves the hit regions.
fn poll_cursor(window: &WebviewWindow, regions: &Mutex<Vec<Rect>>) {
    let mut ignoring: Option<bool> = None;
    loop {
        thread::sleep(HIT_POLL);
        if window.is_visible().is_err() {
            return;
        }
        let (Ok(cursor), Ok(origin)) = (window.cursor_position(), window.inner_position()) else {
            continue;
        };
        let scale = window.scale_factor().unwrap_or(1.0);
        let x = (cursor.x - origin.x as f64) / scale;
        let y = (cursor.y - origin.y as f64) / scale;
        let ignore = !hits(&regions.lock().unwrap(), x, y);
        if ignoring != Some(ignore) && window.set_ignore_cursor_events(ignore).is_ok() {
            ignoring = Some(ignore);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn only_overlay_labels_are_accepted() {
        assert!(is_overlay_label("overlay-pets-cat"));
        assert!(!is_overlay_label("main"));
        assert!(!is_overlay_label("overlay-"));
        assert!(!is_overlay_label("overlay-Upper"));
        assert!(!is_overlay_label("overlay-a/b"));
        assert!(!is_overlay_label(&format!("overlay-{}", "a".repeat(49))));
    }

    #[test]
    fn anchors_stay_inside_the_screen_with_a_margin() {
        let screen = (1000.0, 600.0);
        let size = (200.0, 100.0);
        assert_eq!(anchored_position("top-left", screen, size), (16.0, 16.0));
        assert_eq!(
            anchored_position("bottom-right", screen, size),
            (784.0, 484.0)
        );
        assert_eq!(anchored_position("top-right", screen, size), (784.0, 16.0));
        assert_eq!(
            anchored_position("bottom-left", screen, size),
            (16.0, 484.0)
        );
        assert_eq!(anchored_position("center", screen, size), (400.0, 250.0));
        assert_eq!(anchored_position("nonsense", screen, size), (784.0, 484.0));
    }

    #[test]
    fn hit_regions_are_half_open_rectangles() {
        let regions = [Rect {
            x: 10.0,
            y: 10.0,
            w: 20.0,
            h: 20.0,
        }];
        assert!(hits(&regions, 10.0, 10.0));
        assert!(hits(&regions, 29.9, 29.9));
        assert!(!hits(&regions, 30.0, 20.0));
        assert!(!hits(&regions, 9.9, 20.0));
        assert!(!hits(&[], 0.0, 0.0));
    }

    #[test]
    fn a_spec_needs_a_valid_label_key_and_size() {
        let good = json!({ "label": "overlay-a", "key": "ext:p/o", "width": 10, "height": 9999 });
        let spec = parse_spec(&good).unwrap();
        assert_eq!(spec.size, (MIN_SIZE, MAX_SIZE));
        assert!(spec.on_top && spec.position.is_none());
        assert_eq!(spec.anchor, "bottom-right");
        for bad in [
            json!({ "label": "main", "key": "k", "width": 100, "height": 100 }),
            json!({ "label": "overlay-a", "width": 100, "height": 100 }),
            json!({ "label": "overlay-a", "key": "k", "height": 100 }),
        ] {
            assert!(parse_spec(&bad).is_none());
        }
        let placed = json!({
            "label": "overlay-a", "key": "k", "width": 100, "height": 100,
            "x": 5, "y": 6, "alwaysOnTop": false, "anchor": "center"
        });
        let spec = parse_spec(&placed).unwrap();
        assert_eq!(spec.position, Some((5.0, 6.0)));
        assert!(!spec.on_top);
        assert_eq!(spec.anchor, "center");
    }

    #[test]
    fn regions_are_read_from_a_list_of_quads() {
        let args: Args = Some(vec![json!([[1, 2, 3, 4], [5, 6], "x", [7, 8, 9, 10]])]);
        assert_eq!(
            parse_regions(&args),
            vec![
                Rect {
                    x: 1.0,
                    y: 2.0,
                    w: 3.0,
                    h: 4.0
                },
                Rect {
                    x: 7.0,
                    y: 8.0,
                    w: 9.0,
                    h: 10.0
                },
            ]
        );
        assert!(parse_regions(&None).is_empty());
    }

    #[test]
    fn the_overlay_url_carries_the_key_as_one_encoded_parameter() {
        let url = overlay_url(8001, "ext:pets/cat&x=1").unwrap();
        assert_eq!(url.host_str(), Some("127.0.0.1"));
        assert_eq!(url.port(), Some(8001));
        let pairs: Vec<_> = url.query_pairs().collect();
        assert_eq!(pairs.len(), 1);
        assert_eq!(pairs[0].0, OVERLAY_PARAM);
        assert_eq!(pairs[0].1, "ext:pets/cat&x=1");
    }
}
