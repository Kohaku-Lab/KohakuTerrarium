//! Headless self-check used by installer tests.
//!
//! With `KT_DESKTOP_SMOKE_OUT` set the window stays hidden. Once the backend is
//! ready and the window has loaded it, the shell writes a JSON report to that
//! path and exits, so CI can verify an installed app without a display session.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tauri::{AppHandle, Manager};
use tauri_plugin_updater::UpdaterExt;

use crate::backend::Backend;

const PAGE_TIMEOUT: Duration = Duration::from_secs(120);
const IPC_TIMEOUT: Duration = Duration::from_secs(30);

/// Runs in the app page: a native call from the loopback origin must succeed.
const ACK_SCRIPT: &str =
    "window.__TAURI_INTERNALS__ && window.__TAURI_INTERNALS__.invoke('smoke_ack').catch(() => {})";

pub fn output_path() -> Option<PathBuf> {
    std::env::var_os("KT_DESKTOP_SMOKE_OUT").map(PathBuf::from)
}

/// First status code of `GET path` on the loopback port, without credentials.
pub fn http_status(port: u16, path: &str) -> Option<u16> {
    let mut stream = TcpStream::connect(("127.0.0.1", port)).ok()?;
    stream
        .set_read_timeout(Some(Duration::from_secs(10)))
        .ok()?;
    let request =
        format!("GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n");
    stream.write_all(request.as_bytes()).ok()?;
    let mut head = String::new();
    stream.take(256).read_to_string(&mut head).ok()?;
    head.split_whitespace().nth(1)?.parse().ok()
}

/// With `KT_DESKTOP_SMOKE_UPDATE` set the self-check also fetches the update feed
/// and downloads the advertised build, which verifies its signature.
pub fn update_probe_requested() -> bool {
    std::env::var_os("KT_DESKTOP_SMOKE_UPDATE").is_some()
}

fn update_probe(app: &AppHandle) -> Value {
    tauri::async_runtime::block_on(async {
        let updater = match app.updater() {
            Ok(updater) => updater,
            Err(error) => return json!({ "available": false, "error": error.to_string() }),
        };
        match updater.check().await {
            Ok(Some(update)) => match update.download(|_, _| {}, || {}).await {
                Ok(bytes) => json!({
                    "available": true,
                    "version": update.version,
                    "downloadVerified": true,
                    "bytes": bytes.len(),
                }),
                Err(error) => json!({
                    "available": true,
                    "version": update.version,
                    "downloadVerified": false,
                    "error": error.to_string(),
                }),
            },
            Ok(None) => json!({ "available": false, "downloadVerified": false }),
            Err(error) => json!({ "available": false, "error": error.to_string() }),
        }
    })
}

pub fn report(port: u16, page_loaded: bool, ipc: bool, update: Option<Value>) -> Value {
    let update_ok = update
        .as_ref()
        .is_none_or(|probe| probe["downloadVerified"] == true);
    json!({
        "ok": page_loaded && ipc && update_ok,
        "shellUpdate": update,
        "port": port,
        "pageLoaded": page_loaded,
        "ipcFromAppPage": ipc,
        "unauthenticatedStatus": http_status(port, "/"),
        "healthStatus": http_status(port, "/healthz"),
        "shell": "tauri",
        "version": env!("CARGO_PKG_VERSION"),
    })
}

fn write_report(path: &PathBuf, value: &Value) {
    let _ = std::fs::write(
        path,
        serde_json::to_string_pretty(value).unwrap_or_default(),
    );
}

/// Report once the app page has loaded, then exit.
pub fn finish_when_loaded(app: AppHandle, port: u16, out: PathBuf) {
    thread::spawn(move || {
        let backend = app.state::<Backend>();
        let deadline = Instant::now() + PAGE_TIMEOUT;
        while !backend.page_loaded() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(100));
        }
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.eval(ACK_SCRIPT);
        }
        let ipc_deadline = Instant::now() + IPC_TIMEOUT;
        while !backend.ipc_confirmed() && Instant::now() < ipc_deadline {
            thread::sleep(Duration::from_millis(100));
        }
        let update = update_probe_requested().then(|| update_probe(&app));
        let value = report(port, backend.page_loaded(), backend.ipc_confirmed(), update);
        write_report(&out, &value);
        app.exit(if value["ok"] == true { 0 } else { 1 });
    });
}

pub fn fail(app: &AppHandle, out: &PathBuf, message: &str) {
    write_report(out, &json!({ "ok": false, "error": message }));
    app.exit(1);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    fn serve_once(status_line: &'static str) -> u16 {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                let mut buffer = [0u8; 512];
                let _ = stream.read(&mut buffer);
                let _ = stream
                    .write_all(format!("{status_line}\r\nContent-Length: 0\r\n\r\n").as_bytes());
            }
        });
        port
    }

    #[test]
    fn status_is_the_first_response_line_code() {
        assert_eq!(
            http_status(serve_once("HTTP/1.1 401 Unauthorized"), "/"),
            Some(401)
        );
        assert_eq!(
            http_status(serve_once("HTTP/1.1 200 OK"), "/healthz"),
            Some(200)
        );
    }

    #[test]
    fn an_unreachable_port_has_no_status() {
        let port = TcpListener::bind("127.0.0.1:0")
            .unwrap()
            .local_addr()
            .unwrap()
            .port();
        assert_eq!(http_status(port, "/"), None);
    }

    #[test]
    fn a_report_is_ok_only_when_the_page_loaded_and_could_call_native_commands() {
        for (loaded, ipc) in [(false, false), (true, false), (false, true)] {
            let port = serve_once("HTTP/1.1 401 Unauthorized");
            assert_eq!(report(port, loaded, ipc, None)["ok"], false);
        }
        let port = serve_once("HTTP/1.1 401 Unauthorized");
        let unverified = report(port, true, true, Some(json!({ "downloadVerified": false })));
        assert_eq!(unverified["ok"], false);
        let port = serve_once("HTTP/1.1 401 Unauthorized");
        let verified = report(port, true, true, Some(json!({ "downloadVerified": true })));
        assert_eq!(verified["ok"], true);
        let port = serve_once("HTTP/1.1 401 Unauthorized");
        let healthy = report(port, true, true, None);
        assert_eq!(healthy["ok"], true);
        assert_eq!(healthy["ipcFromAppPage"], true);
        assert_eq!(healthy["unauthenticatedStatus"], 401);
    }
}
