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
const IPC_TIMEOUT: Duration = Duration::from_secs(90);

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

/// What `KT_DESKTOP_SMOKE_UPDATE` asks of the update feed.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum UpdateProbe {
    /// Download the advertised build, which verifies its signature.
    Download,
    /// Install it and restart into it; being current already is a pass.
    Install,
}

pub fn update_probe_requested() -> Option<UpdateProbe> {
    match std::env::var("KT_DESKTOP_SMOKE_UPDATE").ok()?.as_str() {
        "install" => Some(UpdateProbe::Install),
        _ => Some(UpdateProbe::Download),
    }
}

/// Run the probe. `before_install` receives a report to persist before the app
/// is replaced and restarts.
fn update_probe(app: &AppHandle, probe: UpdateProbe, before_install: impl FnOnce(&Value)) -> Value {
    tauri::async_runtime::block_on(async {
        let updater = match app.updater() {
            Ok(updater) => updater,
            Err(error) => return json!({ "available": false, "error": error.to_string() }),
        };
        let update = match updater.check().await {
            Ok(Some(update)) => update,
            Ok(None) => {
                return json!({
                    "available": false,
                    "upToDate": probe == UpdateProbe::Install,
                })
            }
            Err(error) => return json!({ "available": false, "error": error.to_string() }),
        };
        if probe == UpdateProbe::Install {
            before_install(&json!({
                "available": true,
                "version": update.version,
                "installing": true,
            }));
            return match crate::commands::apply_update(app, &update).await {
                Ok(never) => match never {},
                Err(error) => json!({
                    "available": true,
                    "version": update.version,
                    "installed": false,
                    "error": error,
                }),
            };
        }
        match update.download(|_, _| {}, || {}).await {
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
        }
    })
}

/// Open an overlay window and wait for its page to load and call a native command.
fn overlay_probe(app: &AppHandle) -> Value {
    let spec = json!({
        "label": "overlay-smoke",
        "key": "smoke",
        "width": 200,
        "height": 120,
    });
    let opened = crate::overlay::open(app, &spec);
    let backend = app.state::<Backend>();
    let deadline = Instant::now() + IPC_TIMEOUT;
    while opened && !backend.overlay_ipc_confirmed() && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(100));
    }
    json!({
        "opened": opened,
        "pageLoaded": backend.overlay_page_loaded(),
        "ipc": backend.overlay_ipc_confirmed(),
    })
}

pub fn overlay_probe_requested() -> bool {
    std::env::var_os("KT_DESKTOP_SMOKE_OVERLAY").is_some()
}

pub fn menu_probe_requested() -> bool {
    std::env::var_os("KT_DESKTOP_SMOKE_MENU").is_some()
}

/// Build the application menu and count its top-level menus.
fn menu_probe(app: &AppHandle) -> Value {
    match crate::menu::build(app) {
        Ok(menu) => json!({
            "built": true,
            "topLevel": menu.items().map_or(0, |items| items.len()),
            "expected": crate::menu::TOP_LEVEL_MENUS,
        }),
        Err(error) => json!({ "built": false, "error": error.to_string() }),
    }
}

pub fn popout_probe_requested() -> bool {
    std::env::var_os("KT_DESKTOP_SMOKE_POPOUT").is_some()
}

/// Open a session window and wait for its page to load and call a native command.
fn popout_probe(app: &AppHandle) -> Value {
    let spec = json!({ "label": "popout-smoke", "scope": "smoke", "title": "smoke" });
    let opened = crate::popout::open(app, &spec);
    let backend = app.state::<Backend>();
    let deadline = Instant::now() + IPC_TIMEOUT;
    while opened && !backend.popout_ipc_confirmed() && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(100));
    }
    json!({
        "opened": opened,
        "pageLoaded": backend.popout_page_loaded(),
        "ipc": backend.popout_ipc_confirmed(),
    })
}

pub fn summon_probe_requested() -> bool {
    std::env::var_os("KT_DESKTOP_SMOKE_SUMMON").is_some()
}

pub fn tray_probe_requested() -> bool {
    std::env::var_os("KT_DESKTOP_SMOKE_TRAY").is_some()
}

/// Show and clear the attention badge on the tray icon.
fn tray_probe(app: &AppHandle) -> Value {
    let installed = app.tray_by_id("main").is_some();
    let shown = installed && crate::tray::update_badge(app, 1, 2);
    let cleared = installed && crate::tray::update_badge(app, 0, 0);
    json!({ "installed": installed, "badgeShown": shown, "badgeCleared": cleared })
}

/// Results of the optional probes a self-check was asked to run.
#[derive(Clone, Default)]
pub struct Probes {
    pub update: Option<Value>,
    pub overlay: Option<Value>,
    pub tray: Option<Value>,
    pub summon: Option<Value>,
    pub popout: Option<Value>,
    pub menu: Option<Value>,
}

pub fn report(version: &str, port: u16, page_loaded: bool, ipc: bool, probes: Probes) -> Value {
    let update_ok = probes
        .update
        .as_ref()
        .is_none_or(|probe| probe["downloadVerified"] == true || probe["upToDate"] == true);
    let overlay_ok = probes
        .overlay
        .as_ref()
        .is_none_or(|probe| probe["pageLoaded"] == true && probe["ipc"] == true);
    let tray_ok = probes
        .tray
        .as_ref()
        .is_none_or(|probe| probe["badgeShown"] == true && probe["badgeCleared"] == true);
    let summon_ok = probes
        .summon
        .as_ref()
        .is_none_or(|probe| probe["registered"] == true && probe["released"] == true);
    let popout_ok = probes
        .popout
        .as_ref()
        .is_none_or(|probe| probe["pageLoaded"] == true && probe["ipc"] == true);
    let menu_ok = probes
        .menu
        .as_ref()
        .is_none_or(|probe| probe["built"] == true && probe["topLevel"] == probe["expected"]);
    json!({
        "ok": page_loaded && ipc && update_ok && overlay_ok && tray_ok && summon_ok && popout_ok
            && menu_ok,
        "menu": probes.menu,
        "popoutWindow": probes.popout,
        "shellUpdate": probes.update,
        "overlayWindow": probes.overlay,
        "tray": probes.tray,
        "summonShortcut": probes.summon,
        "port": port,
        "pageLoaded": page_loaded,
        "ipcFromAppPage": ipc,
        "unauthenticatedStatus": http_status(port, "/"),
        "healthStatus": http_status(port, "/healthz"),
        "shell": "tauri",
        "version": version,
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
        let ipc_deadline = Instant::now() + IPC_TIMEOUT;
        while !backend.ipc_confirmed() && Instant::now() < ipc_deadline {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.eval(ACK_SCRIPT);
            }
            thread::sleep(Duration::from_millis(500));
        }
        let version = app.package_info().version.to_string();
        let mut probes = Probes {
            overlay: overlay_probe_requested().then(|| overlay_probe(&app)),
            tray: tray_probe_requested().then(|| tray_probe(&app)),
            summon: summon_probe_requested().then(|| crate::summon::probe(&app)),
            popout: popout_probe_requested().then(|| popout_probe(&app)),
            menu: menu_probe_requested().then(|| menu_probe(&app)),
            update: None,
        };
        probes.update = update_probe_requested().map(|probe| {
            update_probe(&app, probe, |interim| {
                let value = report(
                    &version,
                    port,
                    backend.page_loaded(),
                    backend.ipc_confirmed(),
                    Probes {
                        update: Some(interim.clone()),
                        ..probes.clone()
                    },
                );
                write_report(&out, &value);
            })
        });
        let value = report(
            &version,
            port,
            backend.page_loaded(),
            backend.ipc_confirmed(),
            probes,
        );
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
        let ok = |loaded, ipc, probes| {
            let port = serve_once("HTTP/1.1 401 Unauthorized");
            report("1.0.0", port, loaded, ipc, probes)["ok"] == true
        };
        for (loaded, ipc) in [(false, false), (true, false), (false, true)] {
            assert!(!ok(loaded, ipc, Probes::default()));
        }
        for (probe, expected) in [
            (json!({ "pageLoaded": true, "ipc": false }), false),
            (json!({ "pageLoaded": false, "ipc": true }), false),
            (json!({ "pageLoaded": true, "ipc": true }), true),
        ] {
            let probes = Probes {
                overlay: Some(probe),
                ..Probes::default()
            };
            assert_eq!(ok(true, true, probes), expected);
        }
        for (probe, expected) in [
            (json!({ "downloadVerified": false }), false),
            (json!({ "available": false }), false),
            (json!({ "downloadVerified": true }), true),
            (json!({ "available": false, "upToDate": true }), true),
        ] {
            let probes = Probes {
                update: Some(probe),
                ..Probes::default()
            };
            assert_eq!(ok(true, true, probes), expected);
        }
        for (probe, expected) in [
            (json!({ "built": false }), false),
            (
                json!({ "built": true, "topLevel": 5, "expected": 6 }),
                false,
            ),
            (json!({ "built": true, "topLevel": 6, "expected": 6 }), true),
        ] {
            let probes = Probes {
                menu: Some(probe),
                ..Probes::default()
            };
            assert_eq!(ok(true, true, probes), expected);
        }
        for (probe, expected) in [
            (json!({ "pageLoaded": true, "ipc": false }), false),
            (json!({ "pageLoaded": true, "ipc": true }), true),
        ] {
            let probes = Probes {
                popout: Some(probe),
                ..Probes::default()
            };
            assert_eq!(ok(true, true, probes), expected);
        }
        for (probe, expected) in [
            (json!({ "registered": false, "released": false }), false),
            (json!({ "registered": true, "released": false }), false),
            (json!({ "registered": true, "released": true }), true),
        ] {
            let probes = Probes {
                summon: Some(probe),
                ..Probes::default()
            };
            assert_eq!(ok(true, true, probes), expected);
        }
        for (probe, expected) in [
            (json!({ "installed": false }), false),
            (json!({ "badgeShown": true, "badgeCleared": false }), false),
            (json!({ "badgeShown": true, "badgeCleared": true }), true),
        ] {
            let probes = Probes {
                tray: Some(probe),
                ..Probes::default()
            };
            assert_eq!(ok(true, true, probes), expected);
        }
        let port = serve_once("HTTP/1.1 401 Unauthorized");
        let healthy = report("1.2.3", port, true, true, Probes::default());
        assert_eq!(healthy["ok"], true);
        assert_eq!(healthy["version"], "1.2.3");
        assert_eq!(healthy["ipcFromAppPage"], true);
        assert_eq!(healthy["unauthenticatedStatus"], 401);
    }
}
