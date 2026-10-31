//! The diagnostics bundle: versions, logs and settings in one zip, with credentials removed.

use std::fs::File;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tauri::{AppHandle, Manager, WebviewWindow};
use tauri_plugin_dialog::DialogExt;
use zip::write::SimpleFileOptions;
use zip::{ZipArchive, ZipWriter};

use super::describe;
use super::redact::redact;
use crate::backend::{self, Backend};
use crate::commands::Args;
use crate::settings::ShellSettings;

const LOG_LINES: usize = 2000;
const DOCTOR_TIMEOUT: Duration = Duration::from_secs(20);
const DEFAULT_NAME: &str = "kohakuterrarium-diagnostics.zip";

/// The last `count` lines of `text`.
pub fn tail_lines(text: &str, count: usize) -> String {
    let lines: Vec<&str> = text.lines().collect();
    lines[lines.len().saturating_sub(count)..].join("\n")
}

/// A destination the page asked for, when it is an absolute `.zip` path in an existing folder.
pub fn valid_destination(text: &str) -> Option<PathBuf> {
    let path = PathBuf::from(text);
    let is_zip = path
        .extension()
        .is_some_and(|ext| ext.eq_ignore_ascii_case("zip"));
    (path.is_absolute() && is_zip && path.parent().is_some_and(Path::is_dir)).then_some(path)
}

/// Write `files` (name, text) into a zip at `dest`, redacting every file's text.
pub fn write_zip(dest: &Path, files: &[(String, String)]) -> Result<usize, String> {
    let file = File::create(dest).map_err(|error| format!("cannot create the file: {error}"))?;
    let mut zip = ZipWriter::new(file);
    let options = SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
    for (name, text) in files {
        zip.start_file(name, options)
            .map_err(|error| error.to_string())?;
        zip.write_all(redact(text).as_bytes())
            .map_err(|error| error.to_string())?;
    }
    zip.finish().map_err(|error| error.to_string())?;
    Ok(files.len())
}

/// Names and text of every file in the zip at `path`.
pub fn read_zip(path: &Path) -> Result<Vec<(String, String)>, String> {
    let mut archive = ZipArchive::new(File::open(path).map_err(|error| error.to_string())?)
        .map_err(|error| error.to_string())?;
    let mut files = Vec::new();
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index).map_err(|error| error.to_string())?;
        let mut text = String::new();
        entry
            .read_to_string(&mut text)
            .map_err(|error| error.to_string())?;
        let name = entry.name().map_err(|error| error.to_string())?;
        files.push((name.into_owned(), text));
    }
    Ok(files)
}

/// Run the bundled interpreter with `args`, give up after `timeout`, and return its output.
fn run_captured(app: &AppHandle, args: &[&str], timeout: Duration) -> String {
    let scratch = std::env::temp_dir().join(format!("kt-diagnostics-{}.txt", std::process::id()));
    let output = || -> Result<String, String> {
        let log = File::create(&scratch).map_err(|error| error.to_string())?;
        let mut command = backend::python_command(app, args)?;
        command
            .stdout(Stdio::from(log.try_clone().map_err(|e| e.to_string())?))
            .stderr(Stdio::from(log));
        let mut child = command.spawn().map_err(|error| error.to_string())?;
        let deadline = Instant::now() + timeout;
        let finished = loop {
            if child.try_wait().map_err(|e| e.to_string())?.is_some() {
                break true;
            }
            if Instant::now() > deadline {
                let _ = child.kill();
                let _ = child.wait();
                break false;
            }
            thread::sleep(Duration::from_millis(100));
        };
        let text = std::fs::read_to_string(&scratch).unwrap_or_default();
        Ok(if finished {
            text
        } else {
            format!("{text}\n[stopped after {} seconds]", timeout.as_secs())
        })
    };
    let result = output().unwrap_or_else(|error| format!("could not run: {error}"));
    let _ = std::fs::remove_file(&scratch);
    result
}

/// A listing of the installed release trees (names only).
fn version_listing(config: &Path) -> String {
    let versions = config.join("runtime").join("versions");
    let mut names: Vec<String> = std::fs::read_dir(&versions)
        .into_iter()
        .flatten()
        .flatten()
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .collect();
    names.sort();
    if names.is_empty() {
        "(no release trees)".to_string()
    } else {
        names.join("\n")
    }
}

/// Everything the bundle holds, as (name, text) before redaction.
pub fn collect(app: &AppHandle) -> Vec<(String, String)> {
    let config = backend::config_dir(app);
    let backend = app.state::<Backend>();
    let read = |path: PathBuf| std::fs::read_to_string(path).unwrap_or_default();
    let info = json!({
        "shell": {
            "version": app.package_info().version.to_string(),
            "os": std::env::consts::OS,
            "arch": std::env::consts::ARCH,
            "webview": tauri::webview_version().ok(),
        },
        "backend": describe(&backend, None),
        "configDir": config,
    });
    vec![
        (
            "info.json".into(),
            serde_json::to_string_pretty(&info).unwrap_or_default(),
        ),
        (
            "app.log".into(),
            tail_lines(&read(config.join("app.log")), LOG_LINES),
        ),
        (
            "runtime/active.json".into(),
            read(config.join("runtime").join("active")),
        ),
        ("runtime/versions.txt".into(), version_listing(&config)),
        (
            "shell-settings.json".into(),
            app.state::<ShellSettings>().snapshot().to_string(),
        ),
        (
            "doctor.txt".into(),
            run_captured(
                app,
                &["-m", "kohakuterrarium.launcher", "doctor"],
                DOCTOR_TIMEOUT,
            ),
        ),
    ]
}

/// Build the bundle at `dest`; the count of files written.
pub fn export(app: &AppHandle, dest: &Path) -> Result<usize, String> {
    write_zip(dest, &collect(app))
}

/// Ask where to save (or use `requested`), build the bundle there, and report how it went.
pub fn export_interactive(
    app: &AppHandle,
    parent: Option<&WebviewWindow>,
    requested: Option<&str>,
) -> Value {
    let dest = match requested {
        Some(text) => match valid_destination(text) {
            Some(path) => path,
            None => {
                return json!({ "ok": false, "error": "the destination must be an absolute .zip path in an existing folder" })
            }
        },
        None => {
            let mut dialog = app
                .dialog()
                .file()
                .set_file_name(DEFAULT_NAME)
                .add_filter("Zip archive", &["zip"]);
            if let Some(window) = parent {
                dialog = dialog.set_parent(window);
            }
            match dialog
                .blocking_save_file()
                .and_then(|chosen| chosen.into_path().ok())
            {
                Some(path) => path,
                None => return json!({ "ok": false, "cancelled": true }),
            }
        }
    };
    match export(app, &dest) {
        Ok(files) => json!({ "ok": true, "path": dest, "files": files }),
        Err(error) => json!({ "ok": false, "error": error }),
    }
}

/// Plant credentials in the log, build a bundle, and check none survived in any file.
pub fn leak_probe(app: &AppHandle) -> Value {
    const SECRET: &str = "sk-ant-api03-SMOKEPROBEFAKEKEY0123456789abcdef";
    const LAUNCH: &str = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef";
    let log = backend::config_dir(app).join("app.log");
    let planted = format!(
        "\nsmoke-probe provider said {SECRET}\nsmoke-probe GET /?kt_launch={LAUNCH}\n\"api_key\": \"{SECRET}\"\n"
    );
    let appended = std::fs::OpenOptions::new()
        .append(true)
        .create(true)
        .open(&log)
        .and_then(|mut file| file.write_all(planted.as_bytes()))
        .is_ok();
    let dest =
        std::env::temp_dir().join(format!("kt-smoke-diagnostics-{}.zip", std::process::id()));
    let written = export(app, &dest);
    let files = read_zip(&dest).unwrap_or_default();
    let _ = std::fs::remove_file(&dest);
    let names: Vec<&str> = files.iter().map(|(name, _)| name.as_str()).collect();
    let leaked = files
        .iter()
        .any(|(_, text)| text.contains(SECRET) || text.contains(LAUNCH));
    let log_text = files
        .iter()
        .find(|(name, _)| name == "app.log")
        .map(|(_, text)| text.as_str())
        .unwrap_or("");
    json!({
        "planted": appended,
        "written": written.is_ok(),
        "hasInfo": names.contains(&"info.json"),
        "hasLog": log_text.contains("smoke-probe"),
        "leaked": leaked,
    })
}

/// Build a diagnostics bundle: pass a path, or nothing to choose one in a save dialog.
#[tauri::command]
pub async fn export_diagnostics(app: AppHandle, window: WebviewWindow, args: Args) -> Value {
    let requested = args
        .as_ref()
        .and_then(|values| values.first())
        .and_then(Value::as_str)
        .map(str::to_string);
    tauri::async_runtime::spawn_blocking(move || {
        export_interactive(&app, Some(&window), requested.as_deref())
    })
    .await
    .unwrap_or_else(|error| json!({ "ok": false, "error": error.to_string() }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("kt-diag-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn only_the_last_lines_of_a_log_are_kept() {
        let text = (1..=10)
            .map(|n| n.to_string())
            .collect::<Vec<_>>()
            .join("\n");
        assert_eq!(tail_lines(&text, 3), "8\n9\n10");
        assert_eq!(tail_lines(&text, 50), text);
        assert_eq!(tail_lines("", 3), "");
    }

    #[test]
    fn a_destination_must_be_an_absolute_zip_in_an_existing_folder() {
        let dir = scratch("dest");
        let ok = dir.join("out.ZIP");
        assert_eq!(valid_destination(ok.to_str().unwrap()), Some(ok));
        assert_eq!(
            valid_destination(dir.join("out.txt").to_str().unwrap()),
            None
        );
        assert_eq!(
            valid_destination(dir.join("missing").join("out.zip").to_str().unwrap()),
            None
        );
        assert_eq!(valid_destination("relative/out.zip"), None);
        assert_eq!(valid_destination(""), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_zip_holds_every_file_and_no_credential() {
        let dir = scratch("zip");
        let dest = dir.join("bundle.zip");
        let secret = "sk-ant-api03-ZmFrZWtleWZha2VrZXlmYWtla2V5";
        let files = vec![
            ("info.json".to_string(), "{\"v\": 1}".to_string()),
            (
                "app.log".to_string(),
                format!("started\nkey {secret}\n\"api_key\": \"{secret}\""),
            ),
            (
                "nested/name.txt".to_string(),
                "Authorization: Bearer abc123".to_string(),
            ),
        ];
        assert_eq!(write_zip(&dest, &files), Ok(3));

        let back = read_zip(&dest).unwrap();
        let names: Vec<&str> = back.iter().map(|(name, _)| name.as_str()).collect();
        assert_eq!(names, ["info.json", "app.log", "nested/name.txt"]);
        assert!(back
            .iter()
            .all(|(_, text)| !text.contains(secret) && !text.contains("abc123")));
        assert!(back[1].1.starts_with("started"));
        assert_eq!(back[0].1, "{\"v\": 1}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn an_unwritable_destination_is_an_error_not_a_panic() {
        let dir = scratch("bad");
        assert!(write_zip(&dir.join("no").join("such").join("a.zip"), &[]).is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_release_tree_listing_names_folders_or_says_there_are_none() {
        let dir = scratch("tree");
        assert_eq!(version_listing(&dir), "(no release trees)");
        let versions = dir.join("runtime").join("versions");
        std::fs::create_dir_all(versions.join("2.2.1.dev11")).unwrap();
        std::fs::create_dir_all(versions.join("2.0.0.dev5")).unwrap();
        assert_eq!(version_listing(&dir), "2.0.0.dev5\n2.2.1.dev11");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
