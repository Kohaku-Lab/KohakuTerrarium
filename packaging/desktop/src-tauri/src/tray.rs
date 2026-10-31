//! Tray icon, close-to-tray and the tray menu.

use std::sync::Mutex;

use serde_json::Value;
use tauri::image::Image;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{App, AppHandle, Manager, State};

use crate::commands::Args;
use crate::settings::ShellSettings;
use crate::window;

const TRAY_ID: &str = "main";

/// Page event that asks the app to open whatever needs the user first.
const FOCUS_ATTENTION_SCRIPT: &str =
    "window.dispatchEvent(new CustomEvent('kt:desktop-focus-attention'))";

/// Paint a filled dot in the top-right corner of an RGBA image.
pub fn draw_badge(rgba: &mut [u8], width: u32, height: u32, color: [u8; 3]) {
    if rgba.len() < (width * height * 4) as usize {
        return;
    }
    let radius = (width.min(height) as f64) / 4.0;
    let (cx, cy) = (width as f64 - radius, radius);
    for y in 0..height {
        for x in 0..width {
            let (dx, dy) = (x as f64 + 0.5 - cx, y as f64 + 0.5 - cy);
            if dx * dx + dy * dy <= radius * radius {
                let at = ((y * width + x) * 4) as usize;
                rgba[at..at + 4].copy_from_slice(&[color[0], color[1], color[2], 0xff]);
            }
        }
    }
}

fn base_icon() -> Image<'static> {
    tauri::include_image!("../../../src/kohakuterrarium/app_icon.png")
}

fn badged_icon(color: [u8; 3]) -> Image<'static> {
    let base = base_icon();
    let mut rgba = base.rgba().to_vec();
    draw_badge(&mut rgba, base.width(), base.height(), color);
    Image::new_owned(rgba, base.width(), base.height())
}

/// Put `dot` (or none) on the tray icon and `tooltip` under the pointer.
pub fn set_indicator(app: &AppHandle, dot: Option<[u8; 3]>, tooltip: &str) -> bool {
    let Some(tray) = app.tray_by_id(TRAY_ID) else {
        return false;
    };
    let icon = dot.map_or_else(base_icon, badged_icon);
    tray.set_icon(Some(icon)).is_ok() && tray.set_tooltip(Some(tooltip)).is_ok()
}

/// Page event carrying the chosen extension item in `detail.id`.
pub const TRAY_ITEM_EVENT: &str = "kt:desktop-tray-item";
/// Page event for the built-in entries: `detail.kind` is `session`, `new` or `folder`.
pub const TRAY_ACTION_EVENT: &str = "kt:desktop-tray-action";
const ITEM_PREFIX: &str = "ext:";
const WAITING_PREFIX: &str = "waiting:";
const SESSION_PREFIX: &str = "session:";
const MAX_ITEMS: usize = 12;
const MAX_SESSIONS: usize = 5;
const MAX_LABEL: usize = 60;
const MAX_ID: usize = 200;

/// The `(id, label)` pairs of a page-supplied list; invalid, repeated and surplus entries are dropped.
pub fn sanitize_items(value: &Value, max: usize) -> Vec<(String, String)> {
    let mut items: Vec<(String, String)> = Vec::new();
    for entry in value.as_array().into_iter().flatten() {
        let id = entry.get("id").and_then(Value::as_str).unwrap_or("");
        let label = entry
            .get("label")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        if id.is_empty() || id.chars().count() > MAX_ID || label.is_empty() {
            continue;
        }
        if items.iter().any(|(known, _)| known == id) {
            continue;
        }
        let label: String = label.chars().take(MAX_LABEL).collect();
        items.push((id.to_string(), label));
        if items.len() == max {
            break;
        }
    }
    items
}

/// The extension item id a tray menu id names, if it is one of ours.
pub fn item_of(menu_id: &str) -> Option<&str> {
    menu_id.strip_prefix(ITEM_PREFIX)
}

/// What a built-in menu id asks the page to do: `(kind, id)`.
pub fn action_of(menu_id: &str) -> Option<(&'static str, &str)> {
    if let Some(id) = menu_id.strip_prefix(WAITING_PREFIX) {
        return Some(("session", id));
    }
    if let Some(id) = menu_id.strip_prefix(SESSION_PREFIX) {
        return Some(("session", id));
    }
    match menu_id {
        "new" => Some(("new", "")),
        "folder" => Some(("folder", "")),
        _ => None,
    }
}

fn dispatch_script(event: &str, detail: &str) -> String {
    format!("window.dispatchEvent(new CustomEvent('{event}', {{ detail: {detail} }}))")
}

/// Script that delivers the chosen extension item to the page.
pub fn item_script(id: &str) -> String {
    let id = serde_json::to_string(id).unwrap_or_else(|_| "\"\"".to_string());
    dispatch_script(TRAY_ITEM_EVENT, &format!("{{ id: {id} }}"))
}

/// Script that delivers a built-in menu choice to the page.
pub fn action_script(kind: &str, id: &str) -> String {
    let encode = |text: &str| serde_json::to_string(text).unwrap_or_else(|_| "\"\"".to_string());
    dispatch_script(
        TRAY_ACTION_EVENT,
        &format!("{{ kind: {}, id: {} }}", encode(kind), encode(id)),
    )
}

/// One line of the tray menu, before it becomes a native item.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Entry {
    Header(String),
    Item { id: String, label: String },
    Separator,
}

fn item(id: impl Into<String>, label: impl Into<String>) -> Entry {
    Entry::Item {
        id: id.into(),
        label: label.into(),
    }
}

/// The tray menu for the page's sessions and extension items, top to bottom.
pub fn layout(
    waiting: &[(String, String)],
    sessions: &[(String, String)],
    extensions: &[(String, String)],
) -> Vec<Entry> {
    let mut entries = vec![item("show", "Show KohakuTerrarium"), Entry::Separator];
    let others: Vec<&(String, String)> = sessions
        .iter()
        .filter(|(id, _)| !waiting.iter().any(|(known, _)| known == id))
        .collect();
    if !waiting.is_empty() {
        entries.push(Entry::Header("Waiting for you".into()));
        for (id, label) in waiting {
            entries.push(item(format!("{WAITING_PREFIX}{id}"), label));
        }
    }
    if !others.is_empty() {
        entries.push(Entry::Header("Sessions".into()));
        for (id, label) in others {
            entries.push(item(format!("{SESSION_PREFIX}{id}"), label));
        }
    }
    if !waiting.is_empty() || !sessions.is_empty() {
        entries.push(Entry::Separator);
    }
    entries.push(item("new", "New session"));
    entries.push(item("folder", "Open folder as session..."));
    entries.push(Entry::Separator);
    if !extensions.is_empty() {
        for (id, label) in extensions {
            entries.push(item(format!("{ITEM_PREFIX}{id}"), label));
        }
        entries.push(Entry::Separator);
    }
    entries.push(item("quit", "Quit"));
    entries
}

/// What the page last told the tray; the menu is rebuilt only when its layout changes.
#[derive(Default)]
pub struct TrayState {
    waiting: Mutex<Vec<(String, String)>>,
    sessions: Mutex<Vec<(String, String)>>,
    extensions: Mutex<Vec<(String, String)>>,
    shown: Mutex<Option<Vec<Entry>>>,
}

impl TrayState {
    fn entries(&self) -> Vec<Entry> {
        layout(
            &self.waiting.lock().unwrap(),
            &self.sessions.lock().unwrap(),
            &self.extensions.lock().unwrap(),
        )
    }
}

fn build_menu<M: Manager<tauri::Wry>>(
    app: &M,
    entries: &[Entry],
) -> tauri::Result<Menu<tauri::Wry>> {
    let menu = Menu::new(app)?;
    for entry in entries {
        match entry {
            Entry::Header(label) => menu.append(&MenuItem::with_id(
                app,
                "header",
                label,
                false,
                None::<&str>,
            )?)?,
            Entry::Item { id, label } => {
                menu.append(&MenuItem::with_id(app, id, label, true, None::<&str>)?)?
            }
            Entry::Separator => menu.append(&PredefinedMenuItem::separator(app)?)?,
        }
    }
    Ok(menu)
}

fn rebuild(app: &AppHandle, state: &TrayState) -> bool {
    let entries = state.entries();
    if state.shown.lock().unwrap().as_ref() == Some(&entries) {
        return true;
    }
    let Some(tray) = app.tray_by_id(TRAY_ID) else {
        return false;
    };
    let applied = build_menu(app, &entries).is_ok_and(|menu| tray.set_menu(Some(menu)).is_ok());
    if applied {
        *state.shown.lock().unwrap() = Some(entries);
    }
    applied
}

pub fn install(app: &App) -> tauri::Result<()> {
    let state = app.state::<TrayState>();
    let entries = state.entries();
    let menu = build_menu(app, &entries)?;
    *state.shown.lock().unwrap() = Some(entries);
    TrayIconBuilder::with_id(TRAY_ID)
        .icon(base_icon())
        .tooltip("KohakuTerrarium")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| {
            let id = event.id.as_ref();
            match id {
                "show" => window::focus(app),
                "quit" => {
                    app.state::<ShellSettings>().begin_quit();
                    app.exit(0);
                }
                other => {
                    let script = match (item_of(other), action_of(other)) {
                        (Some(item), _) => Some(item_script(item)),
                        (None, Some((kind, id))) => Some(action_script(kind, id)),
                        _ => None,
                    };
                    if let (Some(script), Some(main)) = (script, app.get_webview_window("main")) {
                        window::focus(app);
                        let _ = main.eval(script);
                    }
                }
            }
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                let app = tray.app_handle();
                window::focus(app);
                if let Some(main) = app.get_webview_window("main") {
                    let _ = main.eval(FOCUS_ATTENTION_SCRIPT);
                }
            }
        })
        .build(app)?;
    Ok(())
}

#[tauri::command]
pub fn get_close_to_tray(settings: State<ShellSettings>) -> bool {
    settings.close_to_tray()
}

#[tauri::command]
pub fn set_close_to_tray(settings: State<ShellSettings>, args: Args) -> bool {
    args.as_ref()
        .and_then(|values| values.first())
        .and_then(Value::as_bool)
        .is_some_and(|enabled| settings.set_close_to_tray(enabled))
}

/// Replaces the extension items in the tray menu with the `[{id, label}, …]` list given.
#[tauri::command]
pub fn set_tray_items(app: AppHandle, state: State<TrayState>, args: Args) -> bool {
    *state.extensions.lock().unwrap() = args
        .as_ref()
        .and_then(|values| values.first())
        .map(|value| sanitize_items(value, MAX_ITEMS))
        .unwrap_or_default();
    rebuild(&app, &state)
}

/// Replaces the session entries with `{ waiting: [{id, label}], sessions: [{id, label}] }`.
#[tauri::command]
pub fn set_tray_sessions(app: AppHandle, state: State<TrayState>, args: Args) -> bool {
    let model = args.as_ref().and_then(|values| values.first());
    let list = |key: &str| {
        model
            .and_then(|model| model.get(key))
            .map(|value| sanitize_items(value, MAX_SESSIONS))
            .unwrap_or_default()
    };
    set_sessions(&app, &state, list("waiting"), list("sessions"))
}

/// Show these waiting and other sessions in the tray menu.
pub fn set_sessions(
    app: &AppHandle,
    state: &TrayState,
    waiting: Vec<(String, String)>,
    sessions: Vec<(String, String)>,
) -> bool {
    *state.waiting.lock().unwrap() = waiting;
    *state.sessions.lock().unwrap() = sessions;
    rebuild(app, state)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn pairs(entries: &[(&str, &str)]) -> Vec<(String, String)> {
        entries
            .iter()
            .map(|(id, label)| (id.to_string(), label.to_string()))
            .collect()
    }

    fn ids(entries: &[Entry]) -> Vec<String> {
        entries
            .iter()
            .map(|entry| match entry {
                Entry::Item { id, .. } => id.clone(),
                Entry::Header(label) => format!("# {label}"),
                Entry::Separator => "-".into(),
            })
            .collect()
    }

    #[test]
    fn the_dot_covers_the_top_right_corner_and_leaves_the_rest_alone() {
        let (w, h) = (32u32, 32u32);
        let mut rgba = vec![0x11u8; (w * h * 4) as usize];
        draw_badge(&mut rgba, w, h, [0xdc, 0x26, 0x26]);
        let pixel = |x: u32, y: u32| &rgba[((y * w + x) * 4) as usize..][..4];
        assert_eq!(pixel(w - 5, 4), &[0xdc, 0x26, 0x26, 0xff]);
        assert_eq!(pixel(2, h - 2), &[0x11; 4]);
        assert_eq!(pixel(2, 2), &[0x11; 4]);
    }

    #[test]
    fn a_buffer_smaller_than_the_stated_size_is_left_untouched() {
        let mut rgba = vec![0x11u8; 16 * 16 * 4];
        draw_badge(&mut rgba, 32, 32, [0xdc, 0x26, 0x26]);
        assert!(rgba.iter().all(|&byte| byte == 0x11));
        draw_badge(&mut rgba, 16, 16, [0xdc, 0x26, 0x26]);
        assert!(rgba.contains(&0xdc));
    }

    #[test]
    fn page_items_are_trimmed_capped_and_deduplicated() {
        let value = json!([
            { "id": "a", "label": "  Pause bot  " },
            { "id": "a", "label": "again" },
            { "id": "", "label": "no id" },
            { "id": "b", "label": "   " },
            { "id": "c" },
            { "label": "no id key" },
            { "id": "d", "label": "x".repeat(200) },
            "not an object",
        ]);
        let items = sanitize_items(&value, MAX_ITEMS);
        assert_eq!(items.len(), 2);
        assert_eq!(items[0], ("a".to_string(), "Pause bot".to_string()));
        assert_eq!(items[1].1.chars().count(), MAX_LABEL);

        let many: Vec<Value> = (0..40)
            .map(|n| json!({ "id": format!("i{n}"), "label": "x" }))
            .collect();
        assert_eq!(
            sanitize_items(&Value::Array(many.clone()), MAX_ITEMS).len(),
            MAX_ITEMS
        );
        assert_eq!(
            sanitize_items(&Value::Array(many), MAX_SESSIONS).len(),
            MAX_SESSIONS
        );
        assert!(sanitize_items(&Value::Null, MAX_ITEMS).is_empty());
        assert!(sanitize_items(&json!({ "id": "a", "label": "b" }), MAX_ITEMS).is_empty());
        let long_id = json!([{ "id": "i".repeat(MAX_ID + 1), "label": "x" }]);
        assert!(sanitize_items(&long_id, MAX_ITEMS).is_empty());
    }

    #[test]
    fn only_prefixed_menu_ids_belong_to_extensions() {
        assert_eq!(item_of("ext:ext:pkg/bot"), Some("ext:pkg/bot"));
        assert_eq!(item_of("show"), None);
        assert_eq!(item_of("waiting:abc"), None);
    }

    #[test]
    fn built_in_menu_ids_name_what_the_page_should_do() {
        assert_eq!(action_of("waiting:s1"), Some(("session", "s1")));
        assert_eq!(action_of("session:s2"), Some(("session", "s2")));
        assert_eq!(action_of("new"), Some(("new", "")));
        assert_eq!(action_of("folder"), Some(("folder", "")));
        assert_eq!(action_of("quit"), None);
        assert_eq!(action_of("ext:x"), None);
        assert_eq!(action_of("header"), None);
    }

    #[test]
    fn page_scripts_carry_ids_as_json_strings() {
        let script = item_script("pkg\"/x'\\");
        assert!(script.contains(TRAY_ITEM_EVENT));
        assert!(script.contains(r#"id: "pkg\"/x'\\""#));
        assert!(!script.contains("pkg\"/x"));

        let action = action_script("session", "a\"b");
        assert!(action.contains(TRAY_ACTION_EVENT));
        assert!(action.contains(r#"kind: "session""#));
        assert!(action.contains(r#"id: "a\"b""#));
    }

    #[test]
    fn an_empty_tray_still_offers_show_new_open_and_quit() {
        let entries = layout(&[], &[], &[]);
        assert_eq!(ids(&entries), ["show", "-", "new", "folder", "-", "quit"]);
    }

    #[test]
    fn sessions_waiting_for_the_user_come_first_and_are_not_listed_twice() {
        let waiting = pairs(&[("a", "Alpha")]);
        let sessions = pairs(&[("a", "Alpha"), ("b", "Beta")]);
        let entries = layout(&waiting, &sessions, &pairs(&[("pkg/x", "Pause")]));
        assert_eq!(
            ids(&entries),
            [
                "show",
                "-",
                "# Waiting for you",
                "waiting:a",
                "# Sessions",
                "session:b",
                "-",
                "new",
                "folder",
                "-",
                "ext:pkg/x",
                "-",
                "quit"
            ]
        );
    }

    #[test]
    fn a_sessions_section_with_only_waiting_ones_has_no_empty_header() {
        let waiting = pairs(&[("a", "Alpha")]);
        let entries = layout(&waiting, &waiting, &[]);
        assert!(!ids(&entries).contains(&"# Sessions".to_string()));
    }
}
