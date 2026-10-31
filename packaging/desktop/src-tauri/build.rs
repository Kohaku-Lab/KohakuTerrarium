const COMMANDS: &[&str] = &[
    "get_desktop_capabilities",
    "request_desktop_attention",
    "open_external",
    "show_notification",
    "get_autostart",
    "set_autostart",
    "set_desktop_theme",
    "take_deep_link",
    "smoke_ack",
    "check_shell_update",
    "install_shell_update",
    "open_overlay_window",
    "close_overlay_window",
    "overlay_start_drag",
    "overlay_set_bounds",
    "overlay_set_hit_regions",
    "overlay_ack",
];

fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)),
    )
    .expect("failed to run tauri-build");
}
