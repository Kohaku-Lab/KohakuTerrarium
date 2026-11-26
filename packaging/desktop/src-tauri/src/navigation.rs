//! Which top-level navigations may happen inside the app window.
//!
//! The window may show its own bundled shell page and the local backend. Every
//! other web page belongs in the system browser. A backend file or endpoint
//! (`/api/...`) may still load, so a navigation that ends as a download keeps
//! working, but a page that shows one is sent back to the app.

use url::Url;

/// Hosts the bundled shell page is served from (macOS and Linux use the `tauri`
/// scheme, Windows uses an http origin).
const SHELL_HOST: &str = "tauri.localhost";

/// Whether `url` may load in the app window given the backend `port`.
pub fn is_internal(url: &Url, port: Option<u16>) -> bool {
    if crate::development::internal(url) {
        return true;
    }
    match url.scheme() {
        "tauri" | "about" | "asset" | "ipc" | "data" | "blob" => true,
        "http" | "https" => match url.host_str() {
            Some(SHELL_HOST) => true,
            Some("127.0.0.1") => port.is_some() && url.port() == port,
            _ => false,
        },
        _ => false,
    }
}

/// Path prefixes the backend serves itself; every other backend path is a page of the app.
pub const BACKEND_PATH_PREFIXES: [&str; 2] = ["/api/", "/ws/"];

/// Whether `url` is a backend file or endpoint on the backend `port` rather than an app page.
pub fn is_backend_resource(url: &Url, port: Option<u16>) -> bool {
    matches!(url.scheme(), "http" | "https")
        && url.host_str() == Some("127.0.0.1")
        && port.is_some()
        && url.port() == port
        && BACKEND_PATH_PREFIXES
            .iter()
            .any(|prefix| url.path().starts_with(prefix))
}

/// Script that returns a window showing a backend file to the app. It checks the
/// live location, so a navigation that became a download leaves the app alone.
pub const RETURN_TO_APP: &str = r#"(() => {
  if (!/^\/(?:api|ws)\//.test(location.pathname)) return;
  if (history.length > 1) history.back();
  else location.replace("/");
})();"#;

/// Whether the system may be asked to open `url` for the user.
pub fn is_openable(url: &str) -> bool {
    match Url::parse(url) {
        Ok(parsed) => matches!(parsed.scheme(), "http" | "https" | "mailto"),
        Err(_) => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn internal(url: &str, port: Option<u16>) -> bool {
        is_internal(&Url::parse(url).unwrap(), port)
    }

    #[test]
    fn bundled_shell_pages_stay_in_the_window() {
        assert!(internal("tauri://localhost/index.html", None));
        assert!(internal("http://tauri.localhost/index.html", None));
        assert!(internal("about:blank", None));
    }

    #[test]
    fn only_the_backend_port_is_the_app() {
        assert!(internal("http://127.0.0.1:8001/?kt_launch=x", Some(8001)));
        assert!(!internal("http://127.0.0.1:9999/", Some(8001)));
        assert!(!internal("http://127.0.0.1:8001/", None));
    }

    #[test]
    fn foreign_pages_leave_the_window() {
        assert!(!internal("https://example.test/", Some(8001)));
        assert!(!internal("http://localhost:8001/", Some(8001)));
        assert!(!internal("http://127.0.0.1.evil.test:8001/", Some(8001)));
        assert!(!internal("file:///etc/passwd", Some(8001)));
    }

    fn backend_resource(url: &str, port: Option<u16>) -> bool {
        is_backend_resource(&Url::parse(url).unwrap(), port)
    }

    #[test]
    fn backend_files_are_not_app_pages() {
        let port = Some(8001);
        assert!(backend_resource(
            "http://127.0.0.1:8001/api/sessions/s/artifacts/exports/x/pose-overview.jpg",
            port
        ));
        assert!(backend_resource(
            "http://127.0.0.1:8001/api/sessions/s/artifacts/a.zip?x=1",
            port
        ));
        assert!(backend_resource("http://127.0.0.1:8001/ws/logs", port));
    }

    #[test]
    fn app_pages_and_other_origins_are_not_backend_files() {
        let port = Some(8001);
        assert!(!backend_resource("http://127.0.0.1:8001/", port));
        assert!(!backend_resource(
            "http://127.0.0.1:8001/?kt_launch=x",
            port
        ));
        assert!(!backend_resource(
            "http://127.0.0.1:8001/sessions/abc",
            port
        ));
        assert!(!backend_resource("http://127.0.0.1:8001/apidocs", port));
        assert!(!backend_resource("http://127.0.0.1:9999/api/x", port));
        assert!(!backend_resource("http://127.0.0.1:8001/api/x", None));
        assert!(!backend_resource("https://example.test/api/x", port));
        assert!(!backend_resource("tauri://localhost/api/x", port));
    }

    #[test]
    fn the_return_script_checks_the_live_location() {
        assert!(RETURN_TO_APP.contains("location.pathname"));
        assert!(RETURN_TO_APP.contains(r"^\/(?:api|ws)\//"));
        assert!(RETURN_TO_APP.contains("history.back()"));
        assert!(RETURN_TO_APP.contains(r#"location.replace("/")"#));
    }

    #[test]
    fn only_web_and_mail_links_are_openable() {
        assert!(is_openable("https://example.test/a"));
        assert!(is_openable("http://example.test"));
        assert!(is_openable("mailto:a@example.test"));
        assert!(!is_openable("file:///etc/passwd"));
        assert!(!is_openable("javascript:alert(1)"));
        assert!(!is_openable("ms-msdt:/id x"));
        assert!(!is_openable("not a url"));
    }
}
