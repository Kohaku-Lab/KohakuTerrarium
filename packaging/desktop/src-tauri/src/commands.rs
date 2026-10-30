//! Native operations the app page may call.
//!
//! Names and argument shapes mirror the pywebview shell so the frontend's host
//! bridge treats both alike: every command takes its arguments as one `args` array.

use std::sync::Mutex;

use serde_json::{json, Value};
use tauri::{AppHandle, Manager, State, Theme, UserAttentionType, WebviewWindow};
use tauri_plugin_autostart::ManagerExt as AutostartExt;
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_opener::OpenerExt;
use tauri_plugin_updater::UpdaterExt;

use crate::backend::Backend;
use crate::navigation::is_openable;

pub const PROTOCOL: u32 = 1;

/// The last deep link received before the page asked for it.
#[derive(Default)]
pub struct DeepLinks(pub Mutex<Option<String>>);

type Args = Option<Vec<Value>>;

fn string_arg(args: &Args, index: usize) -> Option<&str> {
    args.as_ref()?.get(index)?.as_str()
}

fn bool_arg(args: &Args, index: usize) -> Option<bool> {
    args.as_ref()?.get(index)?.as_bool()
}

pub fn capabilities() -> Value {
    json!({
        "surface": "desktop",
        "protocol": PROTOCOL,
        "shell": "tauri",
        "platform": std::env::consts::OS,
        "nativeAttention": true,
        "openExternal": true,
        "notifications": true,
        "autostart": true,
        "deepLink": true,
    })
}

#[tauri::command]
pub fn get_desktop_capabilities() -> Value {
    capabilities()
}

#[tauri::command]
pub fn request_desktop_attention(window: WebviewWindow) -> bool {
    window
        .request_user_attention(Some(UserAttentionType::Informational))
        .is_ok()
}

#[tauri::command]
pub fn open_external(app: AppHandle, args: Args) -> bool {
    match string_arg(&args, 0) {
        Some(url) if is_openable(url) => app.opener().open_url(url, None::<&str>).is_ok(),
        _ => false,
    }
}

#[tauri::command]
pub fn show_notification(app: AppHandle, args: Args) -> bool {
    let (Some(title), Some(body)) = (string_arg(&args, 0), string_arg(&args, 1)) else {
        return false;
    };
    app.notification()
        .builder()
        .title(title)
        .body(body)
        .show()
        .is_ok()
}

#[tauri::command]
pub fn get_autostart(app: AppHandle) -> bool {
    app.autolaunch().is_enabled().unwrap_or(false)
}

#[tauri::command]
pub fn set_autostart(app: AppHandle, args: Args) -> bool {
    let Some(enabled) = bool_arg(&args, 0) else {
        return false;
    };
    let manager = app.autolaunch();
    let changed = if enabled {
        manager.enable()
    } else {
        manager.disable()
    };
    changed.is_ok() && manager.is_enabled().unwrap_or(false) == enabled
}

#[tauri::command]
pub fn set_desktop_theme(window: WebviewWindow, args: Args) -> bool {
    let Some(dark) = bool_arg(&args, 0) else {
        return false;
    };
    let theme = if dark { Theme::Dark } else { Theme::Light };
    window.set_theme(Some(theme)).is_ok()
}

/// Whether a newer shell build is published for this platform.
#[tauri::command]
pub async fn check_shell_update(app: AppHandle) -> Value {
    let updater = match app.updater() {
        Ok(updater) => updater,
        Err(error) => return json!({ "available": false, "error": error.to_string() }),
    };
    match updater.check().await {
        Ok(Some(update)) => json!({
            "available": true,
            "version": update.version,
            "current": update.current_version,
        }),
        Ok(None) => json!({ "available": false }),
        Err(error) => json!({ "available": false, "error": error.to_string() }),
    }
}

/// Download and install the newer shell build, then restart into it.
#[tauri::command]
pub async fn install_shell_update(app: AppHandle) -> bool {
    let Ok(updater) = app.updater() else {
        return false;
    };
    let Ok(Some(update)) = updater.check().await else {
        return false;
    };
    if update.download_and_install(|_, _| {}, || {}).await.is_err() {
        return false;
    }
    app.state::<Backend>().stop();
    app.restart()
}

/// Lets the headless self-check confirm the app page can call native commands.
#[tauri::command]
pub fn smoke_ack(backend: State<Backend>) -> bool {
    backend.confirm_ipc();
    true
}

#[tauri::command]
pub fn take_deep_link(links: State<DeepLinks>) -> Option<String> {
    links.0.lock().unwrap().take()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn capabilities_declare_the_protocol_the_frontend_expects() {
        let value = capabilities();
        assert_eq!(value["surface"], "desktop");
        assert_eq!(value["protocol"], 1);
        assert_eq!(value["shell"], "tauri");
        assert_eq!(value["nativeAttention"], true);
    }

    #[test]
    fn arguments_are_read_by_position_and_type() {
        let args: Args = Some(vec![json!("title"), json!(true), json!(3)]);
        assert_eq!(string_arg(&args, 0), Some("title"));
        assert_eq!(string_arg(&args, 1), None);
        assert_eq!(bool_arg(&args, 1), Some(true));
        assert_eq!(bool_arg(&args, 2), None);
        assert_eq!(string_arg(&args, 9), None);
        assert_eq!(string_arg(&None, 0), None);
    }
}
