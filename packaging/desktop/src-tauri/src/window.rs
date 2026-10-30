//! The single main window, its navigation policy, and the tray menu.

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::webview::{Color, PageLoadEvent};
use tauri::{App, AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_opener::OpenerExt;

use crate::backend::Backend;
use crate::navigation::{is_internal, is_openable};
use crate::smoke;

/// Keeps the native caption in step with the page's light or dark mode.
const THEME_SYNC: &str = r#"(() => {
  if (window.top !== window || location.hostname !== "127.0.0.1") return;
  const invoke = window.__TAURI_INTERNALS__ && window.__TAURI_INTERNALS__.invoke;
  if (!invoke) return;
  let last;
  const sync = () => {
    const dark = document.documentElement.classList.contains("dark");
    if (dark === last) return;
    last = dark;
    invoke("set_desktop_theme", { args: [dark] }).catch(() => {});
  };
  const start = () => {
    new MutationObserver(sync).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    sync();
  };
  if (document.documentElement) start();
  else document.addEventListener("DOMContentLoaded", start, { once: true });
})();"#;

pub fn create(app: &App) -> tauri::Result<()> {
    let navigation = app.handle().clone();
    let page_load = app.handle().clone();
    WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("KohakuTerrarium")
        .inner_size(1280.0, 800.0)
        .min_inner_size(800.0, 500.0)
        .background_color(Color(0xe3, 0xdd, 0xd5, 0xff))
        .initialization_script(THEME_SYNC)
        .on_navigation(move |url| {
            let port = navigation.state::<Backend>().port();
            if is_internal(url, port) {
                return true;
            }
            if is_openable(url.as_str()) {
                let _ = navigation.opener().open_url(url.as_str(), None::<&str>);
            }
            false
        })
        .visible(smoke::output_path().is_none())
        .on_page_load(move |_window, payload| {
            if !matches!(payload.event(), PageLoadEvent::Finished) {
                return;
            }
            let backend = page_load.state::<Backend>();
            if payload.url().host_str() == Some("127.0.0.1") {
                backend.mark_page_loaded();
            } else {
                backend.mark_shell_loaded();
            }
        })
        .build()?;
    Ok(())
}

pub fn focus(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

pub fn install_tray(app: &App) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, "show", "Show KohakuTerrarium", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &quit])?;
    TrayIconBuilder::with_id("main")
        .icon(tauri::include_image!(
            "../../../src/kohakuterrarium/app_icon.png"
        ))
        .tooltip("KohakuTerrarium")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => focus(app),
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                focus(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}
