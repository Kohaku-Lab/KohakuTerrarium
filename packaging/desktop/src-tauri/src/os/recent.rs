//! Tells the operating system which session files were opened, for its "recent" lists.

use std::path::{Path, PathBuf};

use crate::commands::{string_arg, Args};
use crate::opens::SESSION_EXTENSION;

/// A session file the page names: an absolute path to an existing `.kohakutr` file.
pub fn valid_session_path(text: &str) -> Option<PathBuf> {
    let path = PathBuf::from(text);
    let is_session = path
        .extension()
        .is_some_and(|ext| ext.eq_ignore_ascii_case(SESSION_EXTENSION));
    (path.is_absolute() && is_session && path.is_file()).then_some(path)
}

#[cfg(windows)]
fn add(path: &Path) -> bool {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::UI::Shell::{SHAddToRecentDocs, SHARD_PATHW};

    let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    // SAFETY: `wide` is a NUL-terminated UTF-16 path that outlives the call.
    unsafe { SHAddToRecentDocs(SHARD_PATHW as u32, wide.as_ptr().cast()) };
    true
}

#[cfg(not(windows))]
fn add(_path: &Path) -> bool {
    false
}

/// Add a session file to the system's recent documents (the jump list on Windows).
#[tauri::command]
pub fn add_recent_session(args: Args) -> bool {
    string_arg(&args, 0)
        .and_then(valid_session_path)
        .is_some_and(|path| add(&path))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_existing_absolute_session_files_are_accepted() {
        let dir = std::env::temp_dir().join(format!("kt-recent-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let session = dir.join("chat.kohakutr");
        std::fs::write(&session, "x").unwrap();
        let other = dir.join("notes.txt");
        std::fs::write(&other, "x").unwrap();

        assert_eq!(
            valid_session_path(session.to_str().unwrap()),
            Some(session.clone())
        );
        assert_eq!(valid_session_path(other.to_str().unwrap()), None);
        assert_eq!(
            valid_session_path(dir.join("gone.kohakutr").to_str().unwrap()),
            None
        );
        assert_eq!(valid_session_path("chat.kohakutr"), None);
        assert_eq!(valid_session_path(""), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_session_file_is_added_where_the_system_has_recent_documents() {
        let dir = std::env::temp_dir().join(format!("kt-recent-add-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let session = dir.join("chat.kohakutr");
        std::fs::write(&session, "x").unwrap();
        let args = Some(vec![serde_json::json!(session.to_str().unwrap())]);
        assert_eq!(add_recent_session(args), cfg!(windows));
        assert!(!add_recent_session(None));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
