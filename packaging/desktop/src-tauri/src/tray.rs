//! Tray icon, close-to-tray and the attention badge.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use serde_json::{json, Value};
use tauri::image::Image;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{App, AppHandle, Manager, State};

use crate::commands::{number_arg, Args};
use crate::window;

const SETTINGS_FILE: &str = "shell-settings.json";
const TRAY_ID: &str = "main";
const PENDING_COLOR: [u8; 3] = [0xdc, 0x26, 0x26];
const DONE_COLOR: [u8; 3] = [0x25, 0x63, 0xeb];

/// Page event that asks the app to open whatever needs the user first.
const FOCUS_ATTENTION_SCRIPT: &str =
    "window.dispatchEvent(new CustomEvent('kt:desktop-focus-attention'))";

/// Shell preferences that outlive a run, stored next to the window state.
#[derive(Default)]
pub struct ShellSettings {
    close_to_tray: AtomicBool,
    quitting: AtomicBool,
    path: Mutex<Option<PathBuf>>,
}

impl ShellSettings {
    pub fn load(&self, directory: PathBuf) {
        let path = directory.join(SETTINGS_FILE);
        let enabled = std::fs::read_to_string(&path)
            .ok()
            .and_then(|text| serde_json::from_str::<Value>(&text).ok())
            .and_then(|value| value["closeToTray"].as_bool())
            .unwrap_or(false);
        self.close_to_tray.store(enabled, Ordering::SeqCst);
        *self.path.lock().unwrap() = Some(path);
    }

    pub fn close_to_tray(&self) -> bool {
        self.close_to_tray.load(Ordering::SeqCst)
    }

    pub fn quitting(&self) -> bool {
        self.quitting.load(Ordering::SeqCst)
    }

    pub fn begin_quit(&self) {
        self.quitting.store(true, Ordering::SeqCst);
    }

    /// Whether closing the main window should hide it instead of quitting.
    pub fn hides_on_close(&self) -> bool {
        self.close_to_tray() && !self.quitting()
    }

    fn set_close_to_tray(&self, enabled: bool) -> bool {
        self.close_to_tray.store(enabled, Ordering::SeqCst);
        let guard = self.path.lock().unwrap();
        let Some(path) = guard.as_ref() else {
            return false;
        };
        let text = json!({ "closeToTray": enabled }).to_string();
        path.parent()
            .is_none_or(|parent| std::fs::create_dir_all(parent).is_ok())
            && std::fs::write(path, text).is_ok()
    }
}

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

pub fn install(app: &App) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, "show", "Show KohakuTerrarium", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &quit])?;
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
            _ => {}
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
    fn tooltips_name_what_needs_the_user() {
        assert_eq!(tooltip(0, 0), "KohakuTerrarium");
        assert!(tooltip(2, 0).contains("2 waiting"));
        assert!(tooltip(0, 4).contains("4 finished"));
        let both = tooltip(1, 2);
        assert!(both.contains("1 waiting") && both.contains("2 finished"));
    }

    #[test]
    fn close_to_tray_defaults_off_and_persists() {
        let dir = std::env::temp_dir().join(format!("kt-tray-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let settings = ShellSettings::default();
        settings.load(dir.clone());
        assert!(!settings.close_to_tray() && !settings.hides_on_close());

        assert!(settings.set_close_to_tray(true));
        assert!(settings.hides_on_close());
        let reloaded = ShellSettings::default();
        reloaded.load(dir.clone());
        assert!(reloaded.close_to_tray());

        settings.begin_quit();
        assert!(!settings.hides_on_close());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_corrupt_settings_file_means_the_default() {
        let dir = std::env::temp_dir().join(format!("kt-tray-bad-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join(SETTINGS_FILE), "{not json").unwrap();
        let settings = ShellSettings::default();
        settings.load(dir.clone());
        assert!(!settings.close_to_tray());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn settings_cannot_be_saved_before_the_directory_is_known() {
        assert!(!ShellSettings::default().set_close_to_tray(true));
    }
}
