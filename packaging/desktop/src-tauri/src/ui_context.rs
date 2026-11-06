//! Main-window navigation and focused popout work observations.

use serde_json::Value;
use tauri::{AppHandle, Manager, WebviewWindow};

use crate::commands::Args;
use crate::popout::is_popout_label;

#[tauri::command]
pub fn relay_work_context(window: WebviewWindow, app: AppHandle, args: Args) -> bool {
    if !is_popout_label(window.label()) {
        return false;
    }
    let Some(mut payload) = args.and_then(|a| a.into_iter().next()) else {
        return false;
    };
    if !payload.is_object() || payload.to_string().len() > 16384 {
        return false;
    }
    if payload["selected"] == true && !window.is_focused().unwrap_or(false) {
        payload["selected"] = Value::Bool(false);
    }
    payload["window"] = Value::String(window.label().into());
    app.get_webview_window("main").is_some_and(|main| {
        main.eval(format!(
            "window.dispatchEvent(new CustomEvent('kt:work-context',{{detail:{payload}}}))"
        ))
        .is_ok()
    })
}

#[tauri::command]
pub fn navigate_main(app: AppHandle, args: Args) -> bool {
    let Some(target) = args.and_then(|a| a.into_iter().next()) else {
        return false;
    };
    if target.to_string().len() > 4096
        || !matches!(
            target["kind"].as_str(),
            Some("settings" | "attach" | "extension")
        )
    {
        return false;
    }
    app.get_webview_window("main").is_some_and(|main| {
        let sent = main
            .eval(format!(
                "window.dispatchEvent(new CustomEvent('kt:navigate-main',{{detail:{target}}}))"
            ))
            .is_ok();
        let _ = main.show();
        let _ = main.unminimize();
        let _ = main.set_focus();
        sent
    })
}

/// Page unload cannot reliably send IPC; the shell closes the observation itself.
pub fn popout_closed(app: &AppHandle, label: &str) {
    if let Some(main) = app.get_webview_window("main") {
        let payload = serde_json::json!({"window": label, "closed": true});
        let _ = main.eval(format!(
            "window.dispatchEvent(new CustomEvent('kt:work-context',{{detail:{payload}}}))"
        ));
    }
}
