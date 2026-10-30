//! Which top-level navigations may happen inside the app window.
//!
//! The window may show its own bundled shell page and the local backend. Every
//! other web page belongs in the system browser.

use url::Url;

/// Hosts the bundled shell page is served from (macOS and Linux use the `tauri`
/// scheme, Windows uses an http origin).
const SHELL_HOST: &str = "tauri.localhost";

/// Whether `url` may load in the app window given the backend `port`.
pub fn is_internal(url: &Url, port: Option<u16>) -> bool {
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
