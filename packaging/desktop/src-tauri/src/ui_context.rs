//! Main-window navigation and desktop focus.

use tauri::{AppHandle, Manager, WebviewWindow};

use crate::commands::Args;
use crate::popout::is_popout_label;

#[tauri::command]
pub fn desktop_window_focused(window: WebviewWindow) -> bool {
    (window.label() == "main" || is_popout_label(window.label()))
        && window.is_focused().unwrap_or(false)
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
