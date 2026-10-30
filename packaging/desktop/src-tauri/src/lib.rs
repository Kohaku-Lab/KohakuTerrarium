//! KohakuTerrarium desktop shell: a native window around the local Python backend.

mod backend;
mod commands;
mod navigation;
mod smoke;
mod window;

use tauri::{Manager, RunEvent};
use tauri_plugin_autostart::MacosLauncher;
use tauri_plugin_deep_link::DeepLinkExt;

use crate::backend::Backend;
use crate::commands::DeepLinks;

fn deliver_deep_link(app: &tauri::AppHandle, url: String) {
    *app.state::<DeepLinks>().0.lock().unwrap() = Some(url.clone());
    window::focus(app);
    if let Some(main) = app.get_webview_window("main") {
        let encoded = serde_json::to_string(&url).unwrap_or_else(|_| "\"\"".into());
        let _ = main.eval(format!(
            "window.dispatchEvent(new CustomEvent('kt:deep-link', {{ detail: {{ url: {encoded} }} }}))"
        ));
    }
}

pub fn run() {
    let headless = smoke::output_path().is_some();
    let mut builder = tauri::Builder::default();
    if !headless {
        builder = builder
            .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
                window::focus(app);
            }))
            .plugin(tauri_plugin_window_state::Builder::default().build());
    }
    let app = builder
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            None,
        ))
        .manage(Backend::default())
        .manage(DeepLinks::default())
        .invoke_handler(tauri::generate_handler![
            commands::get_desktop_capabilities,
            commands::request_desktop_attention,
            commands::open_external,
            commands::show_notification,
            commands::get_autostart,
            commands::set_autostart,
            commands::set_desktop_theme,
            commands::take_deep_link,
            commands::smoke_ack,
            commands::check_shell_update,
            commands::install_shell_update,
        ])
        .setup(move |app| {
            window::create(app)?;
            if !headless {
                window::install_tray(app)?;
            }
            let handle = app.handle().clone();
            app.deep_link().on_open_url(move |event| {
                for url in event.urls() {
                    deliver_deep_link(&handle, url.to_string());
                }
            });
            backend::start(app.handle().clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build the KohakuTerrarium desktop app");

    app.run(|app, event| {
        if let RunEvent::Exit = event {
            app.state::<Backend>().stop();
        }
    });
}
