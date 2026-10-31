//! KohakuTerrarium desktop shell: a native window around the local Python backend.

mod attention;
mod backend;
mod commands;
mod dialogs;
mod files;
mod menu;
mod navigation;
mod opens;
mod overlay;
mod popout;
mod settings;
mod smoke;
mod summon;
mod tray;
mod window;

use tauri::{Manager, RunEvent};
use tauri_plugin_autostart::MacosLauncher;
use tauri_plugin_deep_link::DeepLinkExt;

use crate::backend::Backend;
use crate::commands::DeepLinks;
use crate::settings::ShellSettings;

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
            .plugin(tauri_plugin_single_instance::init(|app, args, cwd| {
                window::focus(app);
                let directory = std::path::PathBuf::from(cwd);
                let targets = opens::from_arguments(args.get(1..).unwrap_or_default(), &directory);
                opens::deliver(app, targets);
            }))
            .plugin(tauri_plugin_window_state::Builder::default().build());
    }
    if !headless || smoke::summon_probe_requested() {
        builder = builder.plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, _shortcut, event| {
                    if event.state() == tauri_plugin_global_shortcut::ShortcutState::Pressed {
                        summon::summon(app);
                    }
                })
                .build(),
        );
    }
    let app = builder
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            None,
        ))
        .manage(Backend::default())
        .manage(DeepLinks::default())
        .manage(overlay::HitRegions::default())
        .manage(opens::PendingOpens::default())
        .manage(ShellSettings::default())
        .manage(tray::TrayState::default())
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
            overlay::open_overlay_window,
            overlay::close_overlay_window,
            overlay::overlay_start_drag,
            overlay::overlay_set_bounds,
            overlay::overlay_set_hit_regions,
            overlay::overlay_ack,
            tray::get_close_to_tray,
            tray::set_close_to_tray,
            attention::set_attention_state,
            tray::set_tray_items,
            tray::set_tray_sessions,
            summon::get_summon_shortcut,
            summon::set_summon_shortcut,
            opens::take_pending_opens,
            popout::open_popout_window,
            popout::popout_ack,
            dialogs::pick_folder,
            dialogs::pick_files,
            dialogs::save_file,
            settings::get_shell_settings,
            settings::patch_shell_settings,
        ])
        .on_menu_event(|app, event| menu::on_event(app, &event))
        .on_window_event(|window, event| {
            if window.label() != "main" {
                return;
            }
            match event {
                tauri::WindowEvent::CloseRequested { api, .. }
                    if window
                        .app_handle()
                        .state::<ShellSettings>()
                        .hides_on_close() =>
                {
                    api.prevent_close();
                    let _ = window.hide();
                }
                tauri::WindowEvent::DragDrop(drag) => {
                    if let Some(main) = window.app_handle().get_webview_window("main") {
                        let _ = main.eval(files::script(&files::detail(drag)));
                    }
                }
                tauri::WindowEvent::Destroyed => window.app_handle().exit(0),
                _ => {}
            }
        })
        .setup(move |app| {
            window::create(app)?;
            menu::install(app.handle())?;
            if !headless {
                app.state::<opens::PendingOpens>()
                    .extend(opens::from_launch());
            }
            if let Ok(directory) = app.path().app_config_dir() {
                app.state::<ShellSettings>().load(directory);
            }
            if !headless {
                summon::restore(app.handle());
                tray::install(app)?;
            } else if smoke::tray_probe_requested() {
                let _ = tray::install(app);
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

    app.run(|app, event| match event {
        RunEvent::Exit => app.state::<Backend>().stop(),
        #[cfg(target_os = "macos")]
        RunEvent::Opened { urls } => opens::deliver(app, opens::from_file_urls(&urls)),
        _ => {}
    });
}
