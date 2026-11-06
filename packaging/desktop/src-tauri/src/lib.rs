//! KohakuTerrarium desktop shell: a native window around the local Python backend.

mod attention;
mod backend;
mod chrome;
mod commands;
mod context_menu;
mod development;
mod dialogs;
mod dragout;
mod files;
mod hud;
mod material;
mod menu;
mod navigation;
mod notify;
mod opens;
mod os;
mod overlay;
mod overlay_placement;
mod overlay_store;
mod overlay_viewport;
mod popout;
mod runtime;
mod settings;
mod smoke;
mod summon;
mod tray;
mod ui_context;
mod updater;
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
    development::initialize().expect("invalid desktop development configuration");
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
            .plugin(
                tauri_plugin_window_state::Builder::default()
                    .with_state_flags(
                        tauri_plugin_window_state::StateFlags::all()
                            & !tauri_plugin_window_state::StateFlags::VISIBLE,
                    )
                    .with_filter(|label| !overlay::is_overlay_label(label))
                    .build(),
            );
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
            Some(vec![window::BACKGROUND_FLAG]),
        ))
        .manage(Backend::default())
        .manage(DeepLinks::default())
        .manage(overlay::HitRegions::default())
        .manage(overlay::OverlayWindows::default())
        .manage(opens::PendingOpens::default())
        .manage(ShellSettings::default())
        .manage(overlay_store::OverlayGeometry::default())
        .manage(tray::TrayState::default())
        .manage(popout::PopoutRegistry::default())
        .manage(os::keep_awake::KeepAwakeState::default())
        .invoke_handler(tauri::generate_handler![
            development::source_page_ready,
            commands::get_desktop_capabilities,
            commands::request_desktop_attention,
            commands::open_external,
            commands::show_notification,
            notify::show_action_notification,
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
            overlay::overlay_resize,
            overlay_placement::overlay_placement,
            overlay::overlay_get_bounds,
            overlay_viewport::overlay_get_viewport,
            overlay::overlay_set_hit_regions,
            overlay::overlay_ack,
            overlay::overlay_present,
            overlay::show_overlay_window,
            overlay::overlay_open_settings,
            overlay::reset_overlay_window,
            hud::focus_session,
            ui_context::relay_work_context,
            ui_context::navigate_main,
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
            popout::popout_return,
            chrome::set_window_chrome,
            context_menu::show_context_menu,
            os::keep_awake::set_keep_awake,
            os::recent::add_recent_session,
            os::explorer::get_explorer_verbs,
            os::explorer::set_explorer_verbs,
            runtime::get_backend_status,
            runtime::restart_backend,
            runtime::open_logs,
            runtime::repair::repair_runtime,
            runtime::diagnostics::export_diagnostics,
            dragout::start_file_drag,
            dialogs::pick_folder,
            dialogs::pick_files,
            dialogs::save_file,
            settings::get_shell_settings,
            settings::patch_shell_settings,
        ])
        .on_menu_event(
            |app, event| match context_menu::choice_of(event.id().as_ref()) {
                Some(id) => context_menu::deliver(app, id),
                None => menu::on_event(app, &event),
            },
        )
        .on_window_event(|window, event| {
            if overlay::is_overlay_label(window.label())
                && matches!(event, tauri::WindowEvent::Destroyed)
            {
                overlay::forget(window.app_handle(), window.label());
            }
            if overlay::is_overlay_label(window.label()) {
                if let Some(view) = window.app_handle().get_webview_window(window.label()) {
                    match event {
                        tauri::WindowEvent::Moved(_)
                        | tauri::WindowEvent::Resized(_)
                        | tauri::WindowEvent::ScaleFactorChanged { .. } => {
                            overlay_store::changed(view)
                        }
                        tauri::WindowEvent::CloseRequested { .. } => overlay_store::closing(&view),
                        _ => {}
                    }
                }
                return;
            }
            if popout::is_popout_label(window.label()) {
                if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                    ui_context::popout_closed(window.app_handle(), window.label());
                    window
                        .app_handle()
                        .state::<popout::PopoutRegistry>()
                        .forget(window.label());
                }
                return;
            }
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
            if let Ok(directory) = app.path().app_config_dir() {
                app.state::<ShellSettings>().load(directory);
            }
            window::create(app)?;
            development::watch_owner(app.handle().clone())?;
            menu::install(app.handle())?;
            if !headless {
                app.state::<opens::PendingOpens>()
                    .extend(opens::from_launch());
            }
            if !headless {
                summon::restore(app.handle());
                tray::install(app)?;
                runtime::watch_tray_line(app.handle().clone());
                if !development::active() {
                    runtime::publish_shell_path(app.handle());
                    os::explorer::sync(app.handle());
                }
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
        RunEvent::Exit => {
            popout::remember(app);
            app.state::<Backend>().stop();
        }
        #[cfg(target_os = "macos")]
        RunEvent::Opened { urls } => opens::deliver(app, opens::from_file_urls(&urls)),
        _ => {}
    });
}
