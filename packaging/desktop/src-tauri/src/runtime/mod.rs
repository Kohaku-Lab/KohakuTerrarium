//! Health and control of the backend process, for the app page and the tray.

pub mod diagnostics;
pub mod redact;
pub mod repair;
pub mod stats;

use std::thread;
use std::time::Duration;

use serde_json::{json, Value};
use tauri::{AppHandle, Manager, State};
use tauri_plugin_opener::OpenerExt;

use crate::backend::{self, Backend, Phase};
use crate::tray::{self, TrayState};

/// File in the config folder that tells `kt open` where this app is installed.
pub const SHELL_RECORD: &str = "desktop-shell.json";
const BYTES_PER_MB: u64 = 1024 * 1024;
const LINE_REFRESH: Duration = Duration::from_secs(20);
const LINE_FIRST_DELAY: Duration = Duration::from_secs(5);

/// The tray's one-line summary: "Backend: running - 612 MB".
pub fn status_line(phase: Phase, memory: Option<u64>) -> String {
    match (phase, memory) {
        (Phase::Running, Some(bytes)) => {
            format!("Backend: running - {} MB", bytes / BYTES_PER_MB)
        }
        (phase, _) => format!("Backend: {}", phase.name()),
    }
}

/// Everything the settings panel shows about the backend.
pub fn describe(backend: &Backend, usage: Option<(u64, usize)>) -> Value {
    json!({
        "state": backend.phase().name(),
        "port": backend.port(),
        "pid": backend.pid(),
        "uptimeSec": backend.uptime_secs(),
        "restarts": backend.restarts(),
        "memoryBytes": usage.map(|(bytes, _)| bytes),
        "processes": usage.map(|(_, count)| count),
    })
}

/// Record where this app is installed so `kt open` can hand it folders and session files.
pub fn publish_shell_path(app: &AppHandle) {
    let Ok(exe) = std::env::current_exe() else {
        return;
    };
    let directory = backend::config_dir(app);
    if std::fs::create_dir_all(&directory).is_ok() {
        let _ = std::fs::write(
            directory.join(SHELL_RECORD),
            json!({ "exe": exe }).to_string(),
        );
    }
}

/// Put the current backend summary in the tray menu.
pub fn refresh_tray_line(app: &AppHandle) {
    let backend = app.state::<Backend>();
    let usage = backend.pid().and_then(stats::collect);
    let line = status_line(backend.phase(), usage.map(|(bytes, _)| bytes));
    tray::set_backend_line(app, &app.state::<TrayState>(), line);
}

/// Keep the tray's backend summary current while the app runs.
pub fn watch_tray_line(app: AppHandle) {
    thread::spawn(move || {
        thread::sleep(LINE_FIRST_DELAY);
        while !app.state::<Backend>().is_stopping() {
            refresh_tray_line(&app);
            thread::sleep(LINE_REFRESH);
        }
    });
}

/// Phase, address, uptime, restarts and memory of the backend.
#[tauri::command]
pub fn get_backend_status(backend: State<Backend>) -> Value {
    let usage = backend.pid().and_then(stats::collect);
    describe(&backend, usage)
}

/// Restart the backend in the background; `false` when a restart is already running.
#[tauri::command]
pub fn restart_backend(app: AppHandle) -> bool {
    if app.state::<Backend>().is_relaunching() {
        return false;
    }
    thread::spawn(move || {
        let _ = backend::restart(&app);
    });
    true
}

/// Show the backend log in the file manager.
#[tauri::command]
pub fn open_logs(app: AppHandle) -> bool {
    let directory = backend::config_dir(&app);
    let log = directory.join("app.log");
    let target = if log.is_file() { log } else { directory };
    app.opener().reveal_item_in_dir(target).is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_tray_line_shows_memory_only_while_the_backend_runs() {
        assert_eq!(
            status_line(Phase::Running, Some(612 * BYTES_PER_MB + 5)),
            "Backend: running - 612 MB"
        );
        assert_eq!(status_line(Phase::Running, None), "Backend: running");
        assert_eq!(
            status_line(Phase::Restarting, Some(5 * BYTES_PER_MB)),
            "Backend: restarting"
        );
        assert_eq!(status_line(Phase::Failed, None), "Backend: failed");
    }

    #[test]
    fn a_backend_that_never_started_describes_itself_without_numbers() {
        let backend = Backend::default();
        let value = describe(&backend, None);
        assert_eq!(value["state"], "starting");
        assert_eq!(value["restarts"], 0);
        assert!(value["port"].is_null());
        assert!(value["pid"].is_null());
        assert!(value["memoryBytes"].is_null());

        let measured = describe(&backend, Some((2048, 3)));
        assert_eq!(measured["memoryBytes"], 2048);
        assert_eq!(measured["processes"], 3);
    }
}
