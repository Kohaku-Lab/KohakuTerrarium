//! Tray icon, close-to-tray and the attention badge.

use serde_json::Value;
use tauri::image::Image;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{App, AppHandle, Manager, State};

use crate::commands::{number_arg, Args};
use crate::settings::ShellSettings;
use crate::window;

const TRAY_ID: &str = "main";
const PENDING_COLOR: [u8; 3] = [0xdc, 0x26, 0x26];
const DONE_COLOR: [u8; 3] = [0x25, 0x63, 0xeb];

/// Page event that asks the app to open whatever needs the user first.
const FOCUS_ATTENTION_SCRIPT: &str =
    "window.dispatchEvent(new CustomEvent('kt:desktop-focus-attention'))";

/// Dot colour for the attention counts: waiting for input wins over finished work.
pub fn badge_color(pending: u32, completed: u32) -> Option<[u8; 3]> {
    if pending > 0 {
        Some(PENDING_COLOR)
    } else if completed > 0 {
        Some(DONE_COLOR)
    } else {
        None
    }
}

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

fn tooltip(pending: u32, completed: u32) -> String {
    match (pending, completed) {
        (0, 0) => "KohakuTerrarium".to_string(),
        (p, 0) => format!("KohakuTerrarium - {p} waiting for you"),
        (0, c) => format!("KohakuTerrarium - {c} finished"),
        (p, c) => format!("KohakuTerrarium - {p} waiting for you, {c} finished"),
    }
}

/// Show the counts on the tray icon, its tooltip and the window's taskbar or dock entry.
pub fn update_badge(app: &AppHandle, pending: u32, completed: u32) -> bool {
    let color = badge_color(pending, completed);
    let Some(tray) = app.tray_by_id(TRAY_ID) else {
        return false;
    };
    let icon = color.map_or_else(base_icon, badged_icon);
    let shown = tray.set_icon(Some(icon)).is_ok()
        && tray.set_tooltip(Some(tooltip(pending, completed))).is_ok();
    if let Some(window) = app.get_webview_window("main") {
        #[cfg(target_os = "macos")]
        {
            let total = i64::from(pending) + i64::from(completed);
            let _ = window.set_badge_count((total > 0).then_some(total));
        }
        #[cfg(windows)]
        {
            let dot = color.map(|color| {
                let mut rgba = vec![0u8; 16 * 16 * 4];
                draw_badge(&mut rgba, 16, 16, color);
                Image::new_owned(rgba, 16, 16)
            });
            let _ = window.set_overlay_icon(dot);
        }
        #[cfg(not(any(target_os = "macos", windows)))]
        let _ = window;
    }
    shown
}

/// Page event carrying the chosen extension item in `detail.id`.
pub const TRAY_ITEM_EVENT: &str = "kt:desktop-tray-item";
const ITEM_PREFIX: &str = "ext:";
const MAX_ITEMS: usize = 12;
const MAX_LABEL: usize = 60;
const MAX_ID: usize = 200;

/// The `(id, label)` pairs of a page-supplied item list; invalid, repeated and surplus entries are dropped.
pub fn sanitize_items(value: &Value) -> Vec<(String, String)> {
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
        if items.len() == MAX_ITEMS {
            break;
        }
    }
    items
}

/// The extension item id a tray menu id names, if it is one of ours.
pub fn item_of(menu_id: &str) -> Option<&str> {
    menu_id.strip_prefix(ITEM_PREFIX)
}

/// Script that delivers the chosen extension item to the page.
pub fn item_script(id: &str) -> String {
    let id = serde_json::to_string(id).unwrap_or_else(|_| "\"\"".to_string());
    format!(
        "window.dispatchEvent(new CustomEvent('{TRAY_ITEM_EVENT}', {{ detail: {{ id: {id} }} }}))"
    )
}

fn build_menu<M: Manager<tauri::Wry>>(
    app: &M,
    items: &[(String, String)],
) -> tauri::Result<Menu<tauri::Wry>> {
    let show = MenuItem::with_id(app, "show", "Show KohakuTerrarium", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::new(app)?;
    menu.append(&show)?;
    if !items.is_empty() {
        menu.append(&PredefinedMenuItem::separator(app)?)?;
        for (id, label) in items {
            let item =
                MenuItem::with_id(app, format!("{ITEM_PREFIX}{id}"), label, true, None::<&str>)?;
            menu.append(&item)?;
        }
        menu.append(&PredefinedMenuItem::separator(app)?)?;
    }
    menu.append(&quit)?;
    Ok(menu)
}

pub fn install(app: &App) -> tauri::Result<()> {
    let menu = build_menu(app, &[])?;
    TrayIconBuilder::with_id(TRAY_ID)
        .icon(base_icon())
        .tooltip("KohakuTerrarium")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => window::focus(app),
            "quit" => {
                app.state::<ShellSettings>().begin_quit();
                app.exit(0);
            }
            other => {
                if let (Some(id), Some(main)) = (item_of(other), app.get_webview_window("main")) {
                    let _ = main.eval(item_script(id));
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
pub fn set_tray_items(app: AppHandle, args: Args) -> bool {
    let items = args
        .as_ref()
        .and_then(|values| values.first())
        .map(sanitize_items)
        .unwrap_or_default();
    let Some(tray) = app.tray_by_id(TRAY_ID) else {
        return false;
    };
    build_menu(&app, &items).is_ok_and(|menu| tray.set_menu(Some(menu)).is_ok())
}

/// Shows the `[pending, completed]` attention counts on the tray icon and taskbar entry.
#[tauri::command]
pub fn set_attention_count(app: AppHandle, args: Args) -> bool {
    let count = |index| number_arg(&args, index).map(|n| n.clamp(0.0, 9999.0) as u32);
    let (Some(pending), Some(completed)) = (count(0), count(1)) else {
        return false;
    };
    update_badge(&app, pending, completed)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn waiting_input_outranks_finished_work_for_the_dot() {
        assert_eq!(badge_color(0, 0), None);
        assert_eq!(badge_color(0, 3), Some(DONE_COLOR));
        assert_eq!(badge_color(1, 3), Some(PENDING_COLOR));
        assert_eq!(badge_color(2, 0), Some(PENDING_COLOR));
    }

    #[test]
    fn the_dot_covers_the_top_right_corner_and_leaves_the_rest_alone() {
        let (w, h) = (32u32, 32u32);
        let mut rgba = vec![0x11u8; (w * h * 4) as usize];
        draw_badge(&mut rgba, w, h, PENDING_COLOR);
        let pixel = |x: u32, y: u32| &rgba[((y * w + x) * 4) as usize..][..4];
        assert_eq!(pixel(w - 5, 4), &[0xdc, 0x26, 0x26, 0xff]);
        assert_eq!(pixel(2, h - 2), &[0x11; 4]);
        assert_eq!(pixel(2, 2), &[0x11; 4]);
    }

    #[test]
    fn a_buffer_smaller_than_the_stated_size_is_left_untouched() {
        let mut rgba = vec![0x11u8; 16 * 16 * 4];
        draw_badge(&mut rgba, 32, 32, PENDING_COLOR);
        assert!(rgba.iter().all(|&byte| byte == 0x11));
        draw_badge(&mut rgba, 16, 16, PENDING_COLOR);
        assert!(rgba.contains(&0xdc));
    }

    #[test]
    fn page_items_are_trimmed_capped_and_deduplicated() {
        let value = serde_json::json!([
            { "id": "a", "label": "  Pause bot  " },
            { "id": "a", "label": "again" },
            { "id": "", "label": "no id" },
            { "id": "b", "label": "   " },
            { "id": "c" },
            { "label": "no id key" },
            { "id": "d", "label": "x".repeat(200) },
            "not an object",
        ]);
        let items = sanitize_items(&value);
        assert_eq!(items.len(), 2);
        assert_eq!(items[0], ("a".to_string(), "Pause bot".to_string()));
        assert_eq!(items[1].1.chars().count(), MAX_LABEL);

        let many: Vec<Value> = (0..40)
            .map(|n| serde_json::json!({ "id": format!("i{n}"), "label": "x" }))
            .collect();
        assert_eq!(sanitize_items(&Value::Array(many)).len(), MAX_ITEMS);
        assert!(sanitize_items(&Value::Null).is_empty());
        assert!(sanitize_items(&serde_json::json!({ "id": "a", "label": "b" })).is_empty());
        let long_id = serde_json::json!([{ "id": "i".repeat(MAX_ID + 1), "label": "x" }]);
        assert!(sanitize_items(&long_id).is_empty());
    }

    #[test]
    fn only_prefixed_menu_ids_belong_to_extensions() {
        assert_eq!(item_of("ext:ext:pkg/bot"), Some("ext:pkg/bot"));
        assert_eq!(item_of("show"), None);
        assert_eq!(item_of("quit"), None);
    }

    #[test]
    fn the_page_script_carries_the_id_as_a_json_string() {
        let script = item_script("pkg\"/x'\\");
        assert!(script.contains(TRAY_ITEM_EVENT));
        assert!(script.contains(r#"id: "pkg\"/x'\\""#));
        assert!(!script.contains("pkg\"/x"));
    }

    #[test]
    fn tooltips_name_what_needs_the_user() {
        assert_eq!(tooltip(0, 0), "KohakuTerrarium");
        assert!(tooltip(2, 0).contains("2 waiting"));
        assert!(tooltip(0, 4).contains("4 finished"));
        let both = tooltip(1, 2);
        assert!(both.contains("1 waiting") && both.contains("2 finished"));
    }
}
