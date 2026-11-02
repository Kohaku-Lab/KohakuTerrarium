//! Dragging files out of the app window onto the desktop or into another application.

use std::path::PathBuf;

use serde_json::Value;
use tauri::WebviewWindow;

use crate::commands::Args;

const MAX_FILES: usize = 50;
const PREVIEW_ICON: &[u8] = include_bytes!("../../../../src/kohakuterrarium/app_icon.png");

/// The existing files named by a page-supplied list of absolute paths, at most 50; anything else
/// (relative paths, folders, missing files, non-text) is dropped.
pub fn files_of(spec: &Value) -> Vec<PathBuf> {
    spec.as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(PathBuf::from)
        .filter(|path| path.is_absolute() && path.is_file())
        .filter_map(|path| path.canonicalize().ok())
        .take(MAX_FILES)
        .collect()
}

/// Start a native drag of `[[path, ...]]` from the calling window; `false` when no file qualifies.
/// The drag runs on the main thread and finishes when the pointer is released.
#[tauri::command]
pub fn start_file_drag(window: WebviewWindow, args: Args) -> bool {
    let files = args
        .as_ref()
        .and_then(|values| values.first())
        .map(files_of)
        .unwrap_or_default();
    if files.is_empty() {
        return false;
    }
    let dragged = window.clone();
    window
        .run_on_main_thread(move || {
            let _ = drag::start_drag(
                &dragged,
                drag::DragItem::Files(files),
                drag::Image::Raw(PREVIEW_ICON.to_vec()),
                |_result, _position| {},
                drag::Options::default(),
            );
        })
        .is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("kt-dragout-{name}-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn only_existing_absolute_files_can_be_dragged() {
        let dir = scratch("files");
        let file = dir.join("note.txt");
        std::fs::write(&file, "x").unwrap();
        let spec = json!([
            file.to_string_lossy(),
            dir.to_string_lossy(),
            dir.join("missing.txt").to_string_lossy(),
            "relative/note.txt",
            "",
            7,
        ]);

        let files = files_of(&spec);

        assert_eq!(files.len(), 1);
        assert!(files[0].ends_with("note.txt"));
        assert!(files_of(&json!({})).is_empty());
        assert!(files_of(&Value::Null).is_empty());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn a_drag_carries_no_more_than_fifty_files() {
        let dir = scratch("many");
        let paths: Vec<Value> = (0..MAX_FILES + 5)
            .map(|n| {
                let file = dir.join(format!("f{n}.txt"));
                std::fs::write(&file, "x").unwrap();
                json!(file.to_string_lossy())
            })
            .collect();

        assert_eq!(files_of(&Value::Array(paths)).len(), MAX_FILES);
        std::fs::remove_dir_all(dir).unwrap();
    }
}
