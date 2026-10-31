//! The single main window and its navigation policy.

use tauri::webview::{Color, PageLoadEvent};
use tauri::{App, AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_opener::OpenerExt;

use crate::backend::Backend;
use crate::navigation::{is_internal, is_openable};
use crate::popout;
use crate::settings::ShellSettings;
use crate::smoke;

/// Keeps the native caption in step with the page's light or dark mode.
pub const THEME_SYNC: &str = r#"(() => {
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

/// Argument the login entry carries, so a start at login can stay in the tray.
pub const BACKGROUND_FLAG: &str = "--background";

/// Whether this launch should leave the window hidden: the user asked for it and it is a login start.
pub fn starts_hidden(arguments: &[String], wanted: bool) -> bool {
    wanted && arguments.iter().any(|argument| argument == BACKGROUND_FLAG)
}

pub fn create(app: &App) -> tauri::Result<()> {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    let hidden = starts_hidden(
        &arguments,
        app.state::<ShellSettings>().get_bool("startHidden"),
    );
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
        .visible(smoke::output_path().is_none() && !hidden)
        .on_page_load(move |_window, payload| {
            if !matches!(payload.event(), PageLoadEvent::Finished) {
                return;
            }
            let backend = page_load.state::<Backend>();
            if payload.url().host_str() == Some("127.0.0.1") {
                backend.mark_page_loaded();
                if smoke::output_path().is_none() && backend.claim_popout_restore() {
                    let app = page_load.clone();
                    std::thread::spawn(move || popout::restore(&app));
                }
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

#[cfg(test)]
mod tests {
    use super::*;

    fn arguments(list: &[&str]) -> Vec<String> {
        list.iter().map(|text| text.to_string()).collect()
    }

    #[test]
    fn a_login_start_stays_hidden_only_when_the_user_asked_for_it() {
        let login = arguments(&["--background"]);
        assert!(starts_hidden(&login, true));
        assert!(!starts_hidden(&login, false));
    }

    #[test]
    fn a_start_the_user_made_always_shows_the_window() {
        assert!(!starts_hidden(&[], true));
        assert!(!starts_hidden(
            &arguments(&["C:\\work\\chat.kohakutr"]),
            true
        ));
        assert!(!starts_hidden(&arguments(&["--background-ish"]), true));
    }
}
