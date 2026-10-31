//! Reinstalling the release shipped with the shell while the backend is stopped.

use std::io::{BufRead, BufReader};
use std::process::Stdio;
use std::thread;

use serde_json::Value;
use tauri::{AppHandle, Emitter, Manager};

use crate::backend::{self, Backend};

/// The result line the repair module prints last: `{ "ok": bool, "error": ... }`.
pub fn outcome(line: &str) -> Option<Result<(), String>> {
    let value: Value = serde_json::from_str(line.trim()).ok()?;
    let ok = value.get("ok")?.as_bool()?;
    Some(if ok {
        Ok(())
    } else {
        Err(value
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("the repair failed")
            .to_string())
    })
}

/// Run the launcher's repair module with the bundled interpreter, relaying its progress.
pub fn run(app: &AppHandle) -> Result<(), String> {
    let mut command = backend::python_command(app, &["-m", "kohakuterrarium.launcher.repair"])?;
    let mut child = command
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| format!("cannot start the repair: {error}"))?;
    let stdout = child.stdout.take().ok_or("the repair has no output")?;
    let relay = app.clone();
    let reader = thread::spawn(move || {
        let mut last = None;
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Some(progress) = backend::parse_progress(&line) {
                let _ = relay.emit("kt-progress", progress);
            } else if let Some(result) = outcome(&line) {
                last = Some(result);
            }
        }
        last
    });
    let status = child.wait().map_err(|error| error.to_string())?;
    let result = reader.join().ok().flatten();
    match result {
        Some(Err(message)) => Err(message),
        Some(Ok(())) if status.success() => Ok(()),
        _ => Err(format!(
            "the repair stopped ({status}); details are in app.log"
        )),
    }
}

/// Reinstall the shipped release and start the backend again; `false` when a restart is under way.
#[tauri::command]
pub fn repair_runtime(app: AppHandle) -> bool {
    if app.state::<Backend>().is_relaunching() {
        return false;
    }
    thread::spawn(move || {
        let _ = backend::repair(&app);
    });
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_result_line_is_an_outcome() {
        assert_eq!(
            outcome(r#"{"ok": true, "version": "2.2.1", "error": null}"#),
            Some(Ok(()))
        );
        assert_eq!(
            outcome(r#"{"ok": false, "version": null, "error": "no bundle"}"#),
            Some(Err("no bundle".to_string()))
        );
        assert_eq!(
            outcome(r#"{"ok": false}"#),
            Some(Err("the repair failed".to_string()))
        );
        assert_eq!(outcome(r#"{"phase": "extract", "percent": 20}"#), None);
        assert_eq!(outcome("plain text"), None);
        assert_eq!(outcome(r#"{"ok": "yes"}"#), None);
    }
}
