//! Frameless, transparent, always-on-top windows that draw an extension's overlay
//! outside the main window. Only the main window opens them; each moves itself.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tauri::{
    AppHandle, LogicalPosition, LogicalSize, Manager, PhysicalPosition, PhysicalSize, State,
    WebviewUrl, WebviewWindow, WebviewWindowBuilder,
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
pub struct HitRegions(Mutex<HashMap<String, Arc<Mutex<PointerState>>>>);

#[derive(Clone)]
struct Capture {
    id: String,
    cursor: (f64, f64),
    origin: (i32, i32),
    scale: (f64, f64),
}

#[derive(Clone)]
struct PointerState {
    regions: Vec<Rect>,
    viewport: Option<(f64, f64)>,
    subscribed: bool,
    capture: Option<Capture>,
    dragging: bool,
    updated: Instant,
}

#[derive(Clone)]
struct OverlayIdentity {
    key: String,
    generation: Option<String>,
    content_placement: bool,
}

#[derive(Default)]
pub struct OverlayWindows(Mutex<HashMap<String, OverlayIdentity>>);

impl OverlayWindows {
    fn register(&self, spec: &Spec) {
        self.0.lock().unwrap().insert(
            spec.label.clone(),
            OverlayIdentity {
                key: spec.key.clone(),
                generation: spec.generation.clone(),
                content_placement: spec.content_placement,
            },
        );
    }

    fn matches_generation(&self, label: &str, generation: &str) -> bool {
        self.0
            .lock()
            .unwrap()
            .get(label)
            .is_some_and(|entry| entry.generation.as_deref() == Some(generation))
    }

    fn settings_allowed(&self, label: &str, section: &str) -> bool {
        self.0.lock().unwrap().get(label).is_some_and(|entry| {
            entry.key == "ext:virtual-pet/pet"
                && matches!(section, "ext:virtual-pet/pets" | "overlays")
        })
    }
}

pub fn pointer_supported() -> bool {
    !cfg!(target_os = "linux") || std::env::var_os("WAYLAND_DISPLAY").is_none()
}

pub fn forget(app: &AppHandle, label: &str) {
    app.state::<HitRegions>().0.lock().unwrap().remove(label);
    let removed = app
        .state::<OverlayWindows>()
        .0
        .lock()
        .unwrap()
        .remove(label);
    if let (
        Some(OverlayIdentity {
            key,
            generation: Some(generation),
            ..
        }),
        Some(main),
    ) = (removed, app.get_webview_window("main"))
    {
        dispatch(
            &main,
            "kt:overlay-closed",
            &json!({"key": key, "generation": generation}),
        );
    }
}

fn dispatch(window: &WebviewWindow, event: &str, detail: &Value) -> bool {
    window
        .eval(format!(
            "window.dispatchEvent(new CustomEvent('{}',{{detail:{}}}))",
            event, detail
        ))
        .is_ok()
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Rect {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}

pub fn is_overlay_label(label: &str) -> bool {
    is_label(LABEL_PREFIX, label)
}

/// Whether `label` is `prefix` followed by 1-48 lowercase letters, digits or hyphens.
pub fn is_label(prefix: &str, label: &str) -> bool {
    label.strip_prefix(prefix).is_some_and(|rest| {
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
    generation: Option<String>,
    content_placement: bool,
    host_key: String,
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
        host_key: value
            .get("hostKey")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        generation: value
            .get("generation")
            .and_then(Value::as_str)
            .filter(|v| v.len() <= 128)
            .map(str::to_string),
        content_placement: value
            .get("contentPlacement")
            .and_then(Value::as_bool)
            .unwrap_or(false),
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
    let area = monitor.work_area();
    let origin = &area.position;
    let size = &area.size;
    let screen = (size.width as f64 / scale, size.height as f64 / scale);
    let (x, y) = anchored_position(&spec.anchor, screen, spec.size);
    (origin.x as f64 / scale + x, origin.y as f64 / scale + y)
}

pub fn overlay_url(port: u16, key: &str) -> Option<Url> {
    Url::parse_with_params(&crate::development::origin(port), [(OVERLAY_PARAM, key)]).ok()
}

/// Open the overlay window described by `spec`, or show it if it already exists.
pub fn open(app: &AppHandle, spec: &Value) -> bool {
    let Some(spec) = parse_spec(spec) else {
        return false;
    };
    if let Some(existing) = app.get_webview_window(&spec.label) {
        let _ = crate::overlay_placement::overlay_placement(
            existing.clone(),
            Some(vec![serde_json::json!("validate")]),
        );
        return existing.show().is_ok();
    }
    let Some(mut url) = app
        .state::<Backend>()
        .port()
        .and_then(|port| overlay_url(port, &spec.key))
    else {
        return false;
    };
    if let Some(generation) = &spec.generation {
        url.query_pairs_mut()
            .append_pair("kt_overlay_generation", generation)
            .append_pair("kt_overlay_host", &spec.host_key);
    }
    app.state::<OverlayWindows>().register(&spec);
    url.query_pairs_mut()
        .append_pair("kt_overlay_label", &spec.label);
    let (x, y) = spec
        .position
        .unwrap_or_else(|| default_position(app, &spec));
    let navigation = app.clone();
    let page_load = app.clone();
    let opened = WebviewWindowBuilder::new(app, &spec.label, WebviewUrl::External(url))
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
        .visible(spec.generation.is_none() && smoke::output_path().is_none())
        .on_navigation(move |url| is_internal(url, navigation.state::<Backend>().port()))
        .on_page_load(move |_window, payload| {
            if matches!(payload.event(), tauri::webview::PageLoadEvent::Finished) {
                page_load.state::<Backend>().mark_overlay_page_loaded();
            }
        })
        .build()
        .map(|window| {
            let _ = crate::overlay_placement::overlay_placement(
                window,
                Some(vec![serde_json::json!("restore")]),
            );
        })
        .is_ok();
    if !opened {
        app.state::<OverlayWindows>()
            .0
            .lock()
            .unwrap()
            .remove(&spec.label);
    }
    opened
}

/// Opens an overlay window; called by the main window.
#[tauri::command]
pub async fn open_overlay_window(app: AppHandle, window: WebviewWindow, args: Args) -> bool {
    if window.label() != "main" {
        return false;
    }
    let Some(value) = args.as_ref().and_then(|values| values.first()) else {
        return false;
    };
    let Some(spec) = parse_spec(value) else {
        return false;
    };
    if spec.generation.is_some()
        && app.get_webview_window(&spec.label).is_some()
        && !close_overlay_window(app.clone(), window, Some(vec![json!(spec.label)])).await
    {
        return false;
    }
    open(&app, value)
}

/// Closes the overlay window named by the first argument.
#[tauri::command]
pub async fn close_overlay_window(app: AppHandle, window: WebviewWindow, args: Args) -> bool {
    let Some(label) = string_arg(&args, 0).filter(|label| is_overlay_label(label)) else {
        return false;
    };
    if window.label() != "main" && window.label() != label {
        return false;
    }
    forget(&app, label);
    let Some(overlay) = app.get_webview_window(label) else {
        return true;
    };
    let (tx, rx) = std::sync::mpsc::channel();
    overlay.on_window_event(move |event| {
        if matches!(event, tauri::WindowEvent::Destroyed) {
            let _ = tx.send(());
        }
    });
    if overlay.close().is_err() {
        return false;
    }
    tauri::async_runtime::spawn_blocking(move || rx.recv_timeout(Duration::from_secs(5)).is_ok())
        .await
        .unwrap_or(false)
}

/// Reset native placement without touching viewport geometry or visibility choices.
#[tauri::command]
pub fn reset_overlay_window(app: AppHandle, window: WebviewWindow, args: Args) -> bool {
    if window.label() != "main" {
        return false;
    }
    let Some(spec) = args.as_ref().and_then(|v| v.first()).and_then(parse_spec) else {
        return false;
    };
    let Some(overlay) = app.get_webview_window(&spec.label) else {
        return false;
    };
    if spec.content_placement {
        return crate::overlay_placement::overlay_placement(overlay, Some(vec![json!("reset")]))
            .is_some();
    }
    let (x, y) = default_position(&app, &spec);
    let ok = overlay
        .set_size(LogicalSize::new(spec.size.0, spec.size.1))
        .is_ok()
        && overlay.set_position(LogicalPosition::new(x, y)).is_ok();
    reconcile_position(&overlay);
    ok
}

/// Presents only the renderer matching the current creation generation.
#[tauri::command]
pub fn overlay_present(app: AppHandle, window: WebviewWindow, args: Args) -> bool {
    let state = app.state::<OverlayWindows>();
    let presentations = state.0.lock().unwrap();
    let Some(OverlayIdentity {
        key,
        generation: Some(generation),
        ..
    }) = presentations.get(window.label()).cloned()
    else {
        return false;
    };
    drop(presentations);
    if string_arg(&args, 0) != Some(generation.as_str()) {
        return false;
    }
    let Some(main) = app.get_webview_window("main") else {
        return false;
    };
    if !dispatch(
        &main,
        "kt:overlay-ready",
        &json!({"key": key, "generation": generation}),
    ) {
        return false;
    }
    true
}

/// Main-window acknowledgement after retiring the viewport renderer.
#[tauri::command]
pub fn show_overlay_window(app: AppHandle, window: WebviewWindow, args: Args) -> bool {
    if window.label() != "main" {
        return false;
    }
    let (Some(label), Some(generation)) = (string_arg(&args, 0), string_arg(&args, 1)) else {
        return false;
    };
    if !app
        .state::<OverlayWindows>()
        .matches_generation(label, generation)
    {
        return false;
    }
    app.get_webview_window(label)
        .is_some_and(|overlay| smoke::output_path().is_some() || overlay.show().is_ok())
}

/// Open the pet's registered settings section in the main window.
#[tauri::command]
pub fn overlay_open_settings(app: AppHandle, window: WebviewWindow, args: Args) -> bool {
    let Some(section) = string_arg(&args, 0) else {
        return false;
    };
    if !app
        .state::<OverlayWindows>()
        .settings_allowed(window.label(), section)
    {
        return false;
    }
    let Some(main) = app.get_webview_window("main") else {
        return false;
    };
    if !dispatch(&main, "kt:overlay-settings", &json!({"section": section})) {
        return false;
    }
    crate::window::focus(&app);
    true
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
    let settings = args.as_ref().and_then(|v| v.get(1));
    if settings
        .and_then(|v| v.get("enabled"))
        .and_then(Value::as_bool)
        == Some(false)
    {
        state.0.lock().unwrap().remove(&label);
        return window.set_ignore_cursor_events(false).is_ok();
    }
    let viewport = settings
        .and_then(|v| Some((v.get("width")?.as_f64()?, v.get("height")?.as_f64()?)))
        .filter(|(w, h)| *w > 0.0 && *h > 0.0 && w.is_finite() && h.is_finite());
    let flag = |name| {
        settings
            .and_then(|v| v.get(name))
            .and_then(Value::as_bool)
            .unwrap_or(false)
    };
    let scale = css_scale(&window, viewport);
    let capture = if flag("captured") {
        match (
            window.cursor_position(),
            window.outer_position(),
            window.inner_position(),
        ) {
            (Ok(c), Ok(o), Ok(inner)) => Some(Capture {
                id: settings
                    .and_then(|v| v.get("press"))
                    .and_then(|v| v.get("id"))
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_owned(),
                cursor: press_point(
                    settings.and_then(|v| v.get("press")),
                    (inner.x as f64, inner.y as f64),
                    scale,
                )
                .unwrap_or((c.x, c.y)),
                origin: (o.x, o.y),
                scale,
            }),
            _ => None,
        }
    } else {
        None
    };
    let mut next = PointerState {
        regions,
        viewport,
        subscribed: flag("pointer") && pointer_supported(),
        capture,
        dragging: flag("dragging"),
        updated: Instant::now(),
    };
    let mut tracked = state.0.lock().unwrap();
    if let Some(existing) = tracked.get(&label) {
        let mut current = existing.lock().unwrap();
        if current.updated.elapsed() < Duration::from_secs(2) {
            next.capture = continue_capture(next.capture, current.capture.take());
        }
        *current = next;
        return true;
    }
    let shared = Arc::new(Mutex::new(next));
    tracked.insert(label.clone(), shared.clone());
    thread::spawn(move || {
        poll_cursor(&app, &window, &shared);
        let state = app.state::<HitRegions>();
        let mut tracked = state.0.lock().unwrap();
        if tracked
            .get(&label)
            .is_some_and(|current| Arc::ptr_eq(current, &shared))
        {
            tracked.remove(&label);
        }
    });
    true
}

fn continue_capture(next: Option<Capture>, previous: Option<Capture>) -> Option<Capture> {
    match (next, previous) {
        (Some(next), Some(previous)) if next.id == previous.id => Some(previous),
        (next, _) => next,
    }
}

fn press_point(press: Option<&Value>, origin: (f64, f64), scale: (f64, f64)) -> Option<(f64, f64)> {
    let press = press?;
    let (x, y) = (press.get("x")?.as_f64()?, press.get("y")?.as_f64()?);
    Some((origin.0 + x * scale.0, origin.1 + y * scale.1))
}

fn css_scale(window: &WebviewWindow, viewport: Option<(f64, f64)>) -> (f64, f64) {
    if let (Some((w, h)), Ok(size)) = (viewport, window.inner_size()) {
        if size.width > 0 && size.height > 0 {
            return (size.width as f64 / w, size.height as f64 / h);
        }
    }
    let scale = window.scale_factor().unwrap_or(1.0);
    (scale, scale)
}

fn reachable_position(position: (f64, f64), size: (f64, f64), area: Rect) -> (f64, f64) {
    (
        position
            .0
            .clamp(area.x, area.x + (area.w - size.0).max(0.0)),
        position
            .1
            .clamp(area.y, area.y + (area.h - size.1).max(0.0)),
    )
}

fn reconcile_position(window: &WebviewWindow) {
    let (Ok(origin), Ok(size), Ok(monitors)) = (
        window.outer_position(),
        window.outer_size(),
        window.available_monitors(),
    ) else {
        return;
    };
    let position = (origin.x as f64, origin.y as f64);
    let mut nearest = None;
    let mut distance = f64::INFINITY;
    for monitor in monitors {
        let area = monitor.work_area();
        let rect = Rect {
            x: area.position.x as f64,
            y: area.position.y as f64,
            w: area.size.width as f64,
            h: area.size.height as f64,
        };
        let fitted = (
            f64::from(size.width).min(rect.w),
            f64::from(size.height).min(rect.h),
        );
        let next = reachable_position(position, fitted, rect);
        let delta = (next.0 - position.0).hypot(next.1 - position.1);
        if delta < distance {
            nearest = Some((next, fitted));
            distance = delta;
        }
    }
    if let Some((next, fitted)) = nearest {
        if fitted != (f64::from(size.width), f64::from(size.height)) {
            let _ = window.set_size(PhysicalSize::new(fitted.0 as u32, fitted.1 as u32));
        }
        if next != position {
            let _ = window.set_position(PhysicalPosition::new(next.0 as i32, next.1 as i32));
        }
    }
}

fn local_point(cursor: (f64, f64), origin: (f64, f64), scale: (f64, f64)) -> (f64, f64) {
    (
        (cursor.0 - origin.0) / scale.0,
        (cursor.1 - origin.1) / scale.1,
    )
}

/// Hit testing, screen-space drag movement and subscribed cursor samples share one worker.
fn poll_cursor(app: &AppHandle, window: &WebviewWindow, shared: &Arc<Mutex<PointerState>>) {
    let mut ignoring: Option<bool> = None;
    let mut previous = Value::Null;
    let mut sent = Instant::now();
    let mut geometry_checked = Instant::now() - Duration::from_secs(1);
    loop {
        thread::sleep(HIT_POLL);
        let state = app.state::<HitRegions>();
        if !state
            .0
            .lock()
            .unwrap()
            .get(window.label())
            .is_some_and(|current| Arc::ptr_eq(current, shared))
        {
            return;
        }
        let Ok(visible) = window.is_visible() else {
            return;
        };
        let mut input = {
            let mut state = shared.lock().unwrap();
            if !visible || state.updated.elapsed() > Duration::from_secs(2) {
                state.capture = None;
                state.dragging = false;
            }
            state.clone()
        };
        if visible
            && input.capture.is_none()
            && geometry_checked.elapsed() >= Duration::from_secs(1)
        {
            let content_placement = app
                .state::<OverlayWindows>()
                .0
                .lock()
                .unwrap()
                .get(window.label())
                .is_some_and(|entry| entry.content_placement);
            if content_placement {
                let _ = crate::overlay_placement::overlay_placement(
                    window.clone(),
                    Some(vec![json!("validate")]),
                );
            } else {
                reconcile_position(window);
            }
            geometry_checked = Instant::now();
        }
        let sample = match (window.cursor_position(), window.inner_position()) {
            (Ok(cursor), Ok(origin)) if visible => {
                let scale = css_scale(window, input.viewport);
                let (mut x, mut y) = local_point(
                    (cursor.x, cursor.y),
                    (origin.x as f64, origin.y as f64),
                    scale,
                );
                let (mut dx, mut dy) = (0.0, 0.0);
                if let Some(capture) = &input.capture {
                    (dx, dy) = local_point((cursor.x, cursor.y), capture.cursor, capture.scale);
                    if input.dragging {
                        let _ = window.set_position(PhysicalPosition::new(
                            capture.origin.0 + (cursor.x - capture.cursor.0).round() as i32,
                            capture.origin.1 + (cursor.y - capture.cursor.1).round() as i32,
                        ));
                        if let Ok(origin) = window.inner_position() {
                            (x, y) = local_point(
                                (cursor.x, cursor.y),
                                (origin.x as f64, origin.y as f64),
                                css_scale(window, input.viewport),
                            );
                        }
                    }
                }
                let ignore = input.capture.is_none() && !hits(&input.regions, x, y);
                if ignoring != Some(ignore) && window.set_ignore_cursor_events(ignore).is_ok() {
                    ignoring = Some(ignore);
                }
                json!({"valid": true, "pointerType": "mouse", "x": x, "y": y, "captured": input.capture.is_some(), "captureId": input.capture.as_ref().map(|c| &c.id), "dragX": dx, "dragY": dy})
            }
            _ => {
                input.capture = None;
                input.dragging = false;
                let _ = window.set_ignore_cursor_events(true);
                ignoring = Some(true);
                json!({"valid": false})
            }
        };
        if (input.subscribed || input.capture.is_some())
            && (sample != previous || sent.elapsed() >= Duration::from_millis(300))
        {
            dispatch(window, "kt:overlay-pointer", &sample);
            previous = sample;
            sent = Instant::now();
        }
    }
}

#[cfg(test)]
mod tests;
