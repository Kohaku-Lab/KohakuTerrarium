//! Files and folders the app is asked to open: a double-clicked `.kohakutr` session
//! (file association), a folder passed on the command line, or the same handed to a
//! running instance.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde_json::{json, Value};
use tauri::{AppHandle, Manager, State};

use crate::window;

/// Page event that tells the app new things are waiting in [`PendingOpens`].
pub const OPEN_EVENT: &str = "kt:desktop-open";
pub const SESSION_EXTENSION: &str = "kohakutr";

/// Targets received before the page could take them.
#[derive(Default)]
pub struct PendingOpens(Mutex<Vec<Value>>);

/// What opening `path` means: a session file or a folder to work in.
pub fn classify(path: &Path) -> Option<Value> {
    let kind = if path.is_dir() {
        "workspace"
    } else if path.is_file()
        && path
            .extension()
            .and_then(|extension| extension.to_str())
            .is_some_and(|extension| extension.eq_ignore_ascii_case(SESSION_EXTENSION))
    {
        "session"
    } else {
        return None;
    };
    Some(json!({ "kind": kind, "path": path.to_string_lossy() }))
}

/// Targets named by command-line `arguments` (without the program name).
///
/// Flags and `kt://` links are not paths; relative paths are taken from `directory`.
pub fn from_arguments(arguments: &[String], directory: &Path) -> Vec<Value> {
    arguments
        .iter()
        .filter(|argument| !argument.starts_with('-') && !argument.contains("://"))
        .filter_map(|argument| classify(&directory.join(argument)))
        .collect()
}

/// Local paths named by `file://` URLs (macOS hands opened files over this way).
#[cfg(any(target_os = "macos", test))]
pub fn from_file_urls<'a>(urls: impl IntoIterator<Item = &'a url::Url>) -> Vec<Value> {
    urls.into_iter()
        .filter_map(|url| url.to_file_path().ok())
        .filter_map(|path| classify(&path))
        .collect()
}

/// Queue `targets` for the page, raise the window and tell the page.
pub fn deliver(app: &AppHandle, targets: Vec<Value>) {
    if targets.is_empty() {
        return;
    }
    app.state::<PendingOpens>()
        .0
        .lock()
        .unwrap()
        .extend(targets);
    window::focus(app);
    if let Some(main) = app.get_webview_window("main") {
        let _ = main.eval(format!(
            "window.dispatchEvent(new CustomEvent('{OPEN_EVENT}'))"
        ));
    }
}

/// The program's own arguments as targets, relative to the current directory.
pub fn from_launch() -> Vec<Value> {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    let directory = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    from_arguments(&arguments, &directory)
}

impl PendingOpens {
    pub fn extend(&self, targets: Vec<Value>) {
        self.0.lock().unwrap().extend(targets);
    }
}

/// Hands the page everything queued so far, once.
#[tauri::command]
pub fn take_pending_opens(pending: State<PendingOpens>) -> Vec<Value> {
    std::mem::take(&mut *pending.0.lock().unwrap())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("kt-opens-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn session_files_and_folders_are_targets_and_everything_else_is_not() {
        let dir = scratch("classify");
        let session = dir.join("plan.KohakuTr");
        let text = dir.join("notes.txt");
        let folder = dir.join("project");
        std::fs::write(&session, "x").unwrap();
        std::fs::write(&text, "x").unwrap();
        std::fs::create_dir(&folder).unwrap();

        assert_eq!(classify(&session).unwrap()["kind"], "session");
        assert_eq!(classify(&folder).unwrap()["kind"], "workspace");
        assert!(classify(&text).is_none());
        assert!(classify(&dir.join("missing.kohakutr")).is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_session_named_like_a_folder_is_still_a_folder_only_if_it_is_one() {
        let dir = scratch("dirname");
        let odd = dir.join("looks.kohakutr");
        std::fs::create_dir(&odd).unwrap();
        assert_eq!(classify(&odd).unwrap()["kind"], "workspace");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn arguments_skip_flags_and_links_and_resolve_against_the_directory() {
        let dir = scratch("arguments");
        std::fs::write(dir.join("a.kohakutr"), "x").unwrap();
        std::fs::create_dir(dir.join("work")).unwrap();
        let arguments: Vec<String> = [
            "--flag",
            "kt://session/x",
            "a.kohakutr",
            "work",
            "nothing.kohakutr",
            "https://example.test/a.kohakutr",
        ]
        .map(String::from)
        .to_vec();

        let targets = from_arguments(&arguments, &dir);

        let kinds: Vec<_> = targets
            .iter()
            .map(|t| t["kind"].as_str().unwrap())
            .collect();
        assert_eq!(kinds, ["session", "workspace"]);
        assert!(targets[0]["path"].as_str().unwrap().ends_with("a.kohakutr"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn absolute_arguments_win_over_the_directory() {
        let dir = scratch("absolute");
        let elsewhere = scratch("absolute-elsewhere");
        let session = elsewhere.join("s.kohakutr");
        std::fs::write(&session, "x").unwrap();

        let targets = from_arguments(&[session.to_string_lossy().into_owned()], &dir);

        assert_eq!(targets.len(), 1);
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_dir_all(&elsewhere);
    }

    #[test]
    fn file_urls_become_targets_and_other_urls_do_not() {
        let dir = scratch("urls");
        let session = dir.join("s.kohakutr");
        std::fs::write(&session, "x").unwrap();
        let file = url::Url::from_file_path(&session).unwrap();
        let web = url::Url::parse("https://example.test/s.kohakutr").unwrap();

        let targets = from_file_urls([&file, &web]);

        assert_eq!(targets.len(), 1);
        assert_eq!(targets[0]["kind"], "session");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_page_takes_the_queue_once() {
        let pending = PendingOpens::default();
        pending.extend(vec![json!({ "kind": "session", "path": "/a" })]);
        let taken = std::mem::take(&mut *pending.0.lock().unwrap());
        assert_eq!(taken.len(), 1);
        assert!(pending.0.lock().unwrap().is_empty());
    }
}
